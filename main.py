import aiohttp
import json
import re
import asyncio
from bs4 import BeautifulSoup
from urllib.parse import quote
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from numba import njit

with open('token.txt') as f:
    BOT_TOKEN = f.read().strip()

@njit
def get_jhash(code: int) -> int:
    x = 123456789
    k = 0
    for i in range(1677696):
        x = ((x + code) ^ (x + (x % 3) + (x % 17) + code) ^ i) % 16776960
        if x % 117 == 0:
            k = (k + 1) % 1111
    return k

async def login_to_moodle(username, password):
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"),
        "Accept-Language": "ru,en;q=0.9",
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        login_url = "https://lms.ranepa.ru/login/index.php"
        async with session.get(login_url) as response:
            html = await response.text()
        soup = BeautifulSoup(html, 'html.parser')
        token_input = soup.find('input', {'name': 'logintoken'})
        if not token_input:
            set_cookies = response.headers.getall("Set-Cookie") if hasattr(response.headers, "getall") else [response.headers.get("Set-Cookie", "")]
            js_p = None
            for sc in set_cookies:
                if "__js_p_" in sc:
                    js_p = sc.split("__js_p_=")[1].split(";")[0]
                    break
            print("js_p =", js_p)
            code = int(js_p.split(",")[0])
            jhash = get_jhash(code)
            print("jhash =", jhash)
            session.cookie_jar.update_cookies({"__jua_": quote(headers["User-Agent"], safe=""),"__jhash_": str(jhash),})
            await asyncio.sleep(1)
            async with session.get(login_url, headers={"Referer": "https://www.google.com/"}, allow_redirects=True) as r2:
                html2 = await r2.text()
            soup2 = BeautifulSoup(html2, "html.parser")
            token_input = soup2.find("input", {"name": "logintoken"})
            if token_input:
                pass
            else:
                token_input = None
        logintoken = token_input.get('value', '')
        print(logintoken)
        payload = {
            'logintoken': logintoken,
            'username': username,
            'password': password,
            'anchor': ''
        }
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        post_headers = {**headers, "Referer": login_url, "Origin": "https://lms.ranepa.ru"}
        async with session.post(login_url,data=payload,headers=post_headers,allow_redirects=True) as response:
            print(response.status,response.text)
            if response.status == 200:
                final_html = await response.text()
                if "Неверный логин или пароль" in final_html:
                    return None
                else:
                    return session.cookie_jar
            else:
                return None

async def get_grades(cookie_jar):
    async with aiohttp.ClientSession(cookie_jar=cookie_jar) as session:
        async with session.get(
            "https://lms.ranepa.ru/grade/report/overview/index.php",
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        ) as response:
            content = await response.text()
            soup = BeautifulSoup(content, 'html.parser')
            page_header = soup.find('h1', class_='h2')
            fio = page_header.text.strip() if page_header else "Неизвестный пользователь"
            grades_table = soup.find('table', {'id': 'overview-grade'})
            if not grades_table:
                return "📊 Оценки временно недоступны"
            rows = grades_table.find_all('tr')[1:]
            courses = []
            for row in rows:
                if 'emptyrow' in row.get('class', []):
                    continue
                cells = row.find_all('td')
                if len(cells) >= 2:
                    course_name_elem = cells[0].find('a')
                    course_name = course_name_elem.text.strip() if course_name_elem else cells[0].text.strip()
                    grade = cells[1].text.strip()
                    emoji = "✅" if grade != '-' else "⏳"
                    grade_display = f"{grade}" if grade != '-' else "ожидается"
                    if len(course_name) > 50:
                        course_name = course_name[:47] + "..."
                    
                    courses.append(f"{emoji} {course_name}\n⭐ {grade_display}")
            if not courses:
                return "📭 Нет доступных курсов с оценками"
            result = [f"<b>📊 Оценки {fio}:</b>\n"]
            result.extend(courses)
            result.append(f"\n📈 <b>Всего курсов:</b> {len(courses)}")
            return "\n".join(result)

async def get_sesskey(cookie_jar):
    url = 'https://lms.ranepa.ru/my/'
    async with aiohttp.ClientSession(cookie_jar=cookie_jar) as session:
        async with session.get(url) as response:
            html = await response.text()
            soup = BeautifulSoup(html, 'html.parser')
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string and 'M.cfg' in script.string:
                    start_idx = script.string.find('M.cfg = ')
                    if start_idx != -1:
                        start_idx += len('M.cfg = ')
                        end_idx = script.string.find(';', start_idx)
                        if end_idx != -1:
                            json_str = script.string[start_idx:end_idx].strip()
                            try:
                                config = json.loads(json_str)
                                sesskey = config.get('sesskey')
                                return sesskey
                            except json.JSONDecodeError:
                                pass
            return None

async def get_dashboard(sesskey, cookie_jar):
    url2 = "https://lms.ranepa.ru/my/"
    async with aiohttp.ClientSession(cookie_jar=cookie_jar) as session:
        async with session.post(url2) as response:
            html = await response.text()
            soup = BeautifulSoup(html, 'html.parser')
            profile_link = soup.find('a', title="Просмотр профиля")
            if profile_link:
                fio = profile_link.get_text()
            else:
                fio = None
    url = f"https://lms.ranepa.ru/lib/ajax/service.php?sesskey={sesskey}&info=block_mydashboard_get_enrolled_courses_by_timeline_classification"
    payload = [
        {
            "index": 0,
            "methodname": "block_mydashboard_get_enrolled_courses_by_timeline_classification",
            "args": {
                "offset": 0,
                "limit": 0,
                "classification": "inprogress",
                "sort": "fullname",
                "customfieldname": "",
                "customfieldvalue": ""
            }
        }
    ]
    async with aiohttp.ClientSession(cookie_jar=cookie_jar) as session:
        async with session.post(url,json = payload) as response:
            response_text = await response.text()
            json_response = json.loads(response_text)
            response_obj = json_response[0]
            data = response_obj.get('data', {})
            courses = data.get('courses', [])
            result = f"<b>📚 Курсы{f' {fio}' if fio else ''}:</b>\n"
            for course in courses:
                result += f"{course.get('fullname')}\n➡️ /course_{course.get('id')}\n\n"
            return result

async def get_course(sesskey, cookie_jar,course_id):
    url = f"https://lms.ranepa.ru/lib/ajax/service.php?sesskey={sesskey}&info=core_courseformat_get_state"
    payload = [
        {
            "index": 0,
            "methodname": "core_courseformat_get_state",
            "args": {
                "courseid": course_id
                    }
        }
]
    result = ""
    async with aiohttp.ClientSession(cookie_jar=cookie_jar) as session:
        async with session.post(url,json = payload) as response:
            response_text = await response.text()
            json_response = json.loads(response_text)
            response_obj = json_response[0]
            data_str = response_obj.get('data', '{}')
            data = json.loads(data_str)
            print(data)
            sections = {str(s["id"]): s for s in data.get("section", [])}
            cms = data.get("cm", [])
            cm_by_section = {}
            for cm in cms:
                cm_by_section.setdefault(str(cm.get("sectionid")), []).append(cm)
            lines = []
            stack = []
            sec_order = [str(x) for x in data.get("course", {}).get("sectionlist", [])]
            sec_order = [sid for sid in sec_order if sections.get(sid, {}).get("component") != "mod_subsection"]
            sec_idx = 0
            sec_id, i, depth = (sec_order[0] if sec_order else None), 0, 0
            while sec_id is not None:
                sec = sections.get(sec_id)
                if sec is None:
                    if stack:
                        sec_id, i, depth = stack.pop()
                        continue
                    sec_idx += 1
                    if sec_idx < len(sec_order):
                        sec_id, i, depth = sec_order[sec_idx], 0, 0
                        continue
                    break
                if i == 0:
                    title = sec.get("title") or sec.get("rawtitle") or "Без названия"
                    is_sub = (sec.get("component") == "mod_subsection")
                    indent = "　" * depth
                    icon = "↳ " if is_sub else ""
                    if sec_idx == 0:
                        lines.append(f'<b>📗 Курс "{title}"</b>\n')
                    else:
                        lines.append(f'{indent}{icon}<b>{title}</b>\n')
                sec_cms = cm_by_section.get(sec_id, [])
                if i >= len(sec_cms):
                    if stack:
                        sec_id, i, depth = stack.pop()
                        continue
                    sec_idx += 1
                    if sec_idx < len(sec_order):
                        sec_id, i, depth = sec_order[sec_idx], 0, 0
                        continue
                    break
                cm = sec_cms[i]
                i += 1
                if cm.get("module") == "subsection" and cm.get("delegatesectionid"):
                    stack.append((sec_id, i, depth))
                    sec_id, i, depth = str(cm["delegatesectionid"]), 0, depth + 4
                    continue
                name = cm.get("name", "без названия")
                print(cm)
                emojis = {"attendancernhgs": "💯", "outgrade": "💯", "mtslinkrnhgs": "☎️", "resource": "📄", "workshop": "📤", "vwork": "📝", "quiz": "🧠"}
                emoji = emojis.get(cm.get("module"))
                indent = "　" * depth
                lines.append(f'{indent}{emoji} {name}\n/cm_{cm.get('module')}_{cm.get('id')}\n')

            return "\n".join(lines)

async def get_cm(sesskey,cm_id,cm_type):
    url = f"https://lms.ranepa.ru/mod/{cm_type}/view.php?id={cm_id}"
    
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Используй /grades для получения оценок\nИспользуй /dashboard для получения дашборда")

async def grades_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Получаю оценки...")
    with open('credentials.json') as f:
        username, password = json.load(f)["1"].split(';')
    cookie_jar = await login_to_moodle(username, password)
    if cookie_jar:
        grades = await get_grades(cookie_jar)
        await update.message.reply_text(grades,parse_mode='HTML')
    else:
        await update.message.reply_text("Ошибка авторизации")

async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Получаю дашборд...")
    with open('credentials.json') as f:
        username, password = json.load(f)["1"].split(';')
    cookie_jar = await login_to_moodle(username, password)
    if cookie_jar:
        sesskey = await get_sesskey(cookie_jar)
        result = await get_dashboard(sesskey,cookie_jar)
        await update.message.reply_text(result,parse_mode='HTML')
    else:
        await update.message.reply_text("Ошибка авторизации")

async def open_course(update: Update, context: ContextTypes.DEFAULT_TYPE):
    match = re.match(r"^/course_(\d+)$", update.message.text)
    if match:
        course_id = match.group(1)
        await update.message.reply_text("Получаю курс...")
        with open('credentials.json') as f:
            username, password = json.load(f)["1"].split(';')
        cookie_jar = await login_to_moodle(username, password)
        if cookie_jar:
            sesskey = await get_sesskey(cookie_jar)
            result = await get_course(sesskey,cookie_jar,course_id)
            await update.message.reply_text(result,parse_mode='HTML')
        else:
            await update.message.reply_text("Ошибка авторизации")
    else:
        await update.message.reply_text("Неверный формат команды")

async def open_cm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    match = re.match(r"^/cm_([a-zA-Z]+)_(\d+)$", update.message.text)
    if match:
        cm_type = match.group(1)
        cm_id = match.group(2)
        await update.message.reply_text("Получаю элемент курса...")
        with open('credentials.json') as f:
            username, password = json.load(f)["1"].split(';')
        cookie_jar = await login_to_moodle(username, password)
        if cookie_jar:
            result = await get_cm(cookie_jar,cm_type,cm_id)
    else:
        await update.message.reply_text("Неверный формат команды")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("grades", grades_command))
    app.add_handler(CommandHandler("dashboard",dashboard_command))
    app.add_handler(MessageHandler(filters.Regex(r"^/course_\d+$"), open_course))
    app.add_handler(MessageHandler(filters.Regex(r"^/cm_[a-zA-Z]+_\d+$"), open_cm))
    app.run_polling()

if __name__ == "__main__":
    main()