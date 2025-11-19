import aiohttp
import json
import re
from bs4 import BeautifulSoup
from telegram import Update,InputFile
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from io import BytesIO
from contextlib import suppress
from playwright.async_api import async_playwright
import sqlite3
from dotenv import load_dotenv
import os

load_dotenv()
BOT_TOKEN = os.getenv("TOKEN")

async def send_html_chunks(update, text):
    MAX = 3000
    safe_points = []
    for m in re.finditer(r"</b>|<br>|</i>|</u>|</code>", text):
        safe_points.append(m.end())
    safe_points.append(len(text))
    start = 0
    for p in safe_points:
        if p - start > MAX:
            await update.message.reply_text(text[start:p], parse_mode="HTML")
            start = p
    if start < len(text):
        await update.message.reply_text(text[start:], parse_mode="HTML")

async def login_to_moodle(username, password, user_id):
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
            return None
        logintoken = token_input.get('value', '')
        payload = {
            'logintoken': logintoken,
            'username': username,
            'password': password,
            'anchor': ''
        }
        post_headers = {**headers,"Content-Type": "application/x-www-form-urlencoded","Origin": "https://lms.ranepa.ru","Referer": "https://lms.ranepa.ru/login/index.php"}
        async with session.post(login_url, data=payload, headers=post_headers, allow_redirects=True) as response:
            final_html = await response.text()
            if response.status == 200:
                final_html = await response.text()
                if "Неверный логин или пароль" in final_html:
                    return None
                else:
                    cookies_dict = {c.key: c.value for c in session.cookie_jar}
                    cookies_json = json.dumps(cookies_dict)
                    conn = sqlite3.connect("sessions.db")
                    cursor = conn.cursor()
                    cursor.execute("""INSERT INTO sessions (user_id, cookie_jar)VALUES (?, ?)ON CONFLICT(user_id) DO UPDATE SET cookie_jar = excluded.cookie_jar;""", (user_id, cookies_json))
                    conn.commit()
                    conn.close()
            else:
                return None

async def get_grades(cookie_jar):
    async with aiohttp.ClientSession(cookie_jar=cookie_jar) as session:
        async with session.get(
            "https://lms.ranepa.ru/grade/report/overview/index.php",
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        ) as response:
            if "login/index.php" in str(response.url):
                return "Время вашего сеанса истекло. Пожалуйста, войдите снова."
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
                    emoji = "⏳" if grade == '-' else "⭐"
                    grade_display = f"{grade}" if grade != '-' else "ожидается"
                    if len(course_name) > 60:
                        course_name = course_name[:57] + "..."
                    
                    courses.append(f"\n<b>{course_name}</b>\n{emoji} {grade_display}")
            if not courses:
                return "📭 Нет доступных курсов с оценками"
            result = [f"<b>📊 Оценки {fio}:</b>"]
            result.extend(courses)
            result.append(f"\n📈 <b>Всего курсов:</b> {len(courses)}")
            return "\n".join(result)

async def get_sesskey(cookie_jar,user_id):
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
                                conn = sqlite3.connect("sessions.db")
                                cursor = conn.cursor()
                                cursor.execute("""INSERT INTO sessions (user_id, sesskey)VALUES (?, ?)ON CONFLICT(user_id) DO UPDATE SET sesskey = excluded.sesskey;""", (user_id, sesskey))
                                conn.commit()
                                conn.close()
                            except json.JSONDecodeError:
                                pass
            return None

async def get_dashboard(sesskey, cookie_jar):
    url2 = "https://lms.ranepa.ru/grade/report/overview/index.php"
    async with aiohttp.ClientSession(cookie_jar=cookie_jar) as session:
        async with session.post(url2) as response:
            if "login/index.php" in str(response.url):
                return "Время вашего сеанса истекло. Пожалуйста, войдите снова."
            html = await response.text()
            soup = BeautifulSoup(html, 'html.parser')
            page_header = soup.find('h1', class_='h2')
            fio = page_header.text.strip() if page_header else ""
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
    url = f"https://lms.ranepa.ru/course/view.php?id={course_id}"
    async with aiohttp.ClientSession(cookie_jar=cookie_jar) as session:
        async with session.get(url) as resp:
            if "login/index.php" in str(resp.url):
                return None
            html = await resp.text()
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one("h1.h2.mb-0")
    title = title_el.get_text(strip=True) if title_el else "Без названия"
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
    async with aiohttp.ClientSession(cookie_jar=cookie_jar) as session:
        async with session.post(url,json = payload) as response:
            response_text = await response.text()
            json_response = json.loads(response_text)
            response_obj = json_response[0]
            if response_obj.get("error") and response_obj["exception"]["errorcode"] in (
                "servicerequireslogin",
                "requireloginerror",
                "invalidsesskey"
            ):
                return "Время вашего сеанса истекло. Пожалуйста, войдите снова."
            data_str = response_obj.get('data', '{}')
            data = json.loads(data_str)
            sections = {str(s["id"]): s for s in data.get("section", [])}
            cms = data.get("cm", [])
            cm_by_section = {}
            for cm in cms:
                cm_by_section.setdefault(str(cm.get("sectionid")), []).append(cm)
            lines = []
            lines.append(f'<b>📗 Курс "{title}"</b>\n')
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
                emojis = {"attendancernhgs": "💯", "outgrade": "💯", "mtslinkrnhgs": "☎️", "resource": "📄", "workshop": "📤", "vwork": "📝", "quiz": "🧠", "page":"📜","folder":"📁","video":"🎬", "scorm":"🖥️","label":"🏷️"}
                emoji = emojis.get(cm.get("module"))
                indent = "　" * depth
                lines.append(f'{indent}{emoji} {name}\n/cm_{cm.get('module')}_{cm.get('id')}\n')

            return "\n".join(lines)

async def get_cm(cookie_jar,cm_id,cm_type):
    url = f"https://lms.ranepa.ru/mod/{cm_type}/view.php?id={cm_id}"
    match cm_type:
        case "resource":
            async with aiohttp.ClientSession(cookie_jar=cookie_jar) as session:
                async with session.get(url,allow_redirects=True) as response:
                    if "login/index.php" in str(response.url):
                        return {"type": "error", "text": "Время вашего сеанса истекло. Пожалуйста, войдите снова."}
                    data = await response.read()
                    bio = BytesIO(data)
                    bio.name = response.url.name
                    return {"type": "file","file":InputFile(bio)}
        case "outgrade":
            async with aiohttp.ClientSession(cookie_jar=cookie_jar) as session:
                async with session.get(url) as resp:
                    if "login/index.php" in str(resp.url):
                        return {"type": "error", "text": "Время вашего сеанса истекло. Пожалуйста, войдите снова."}
                    html = await resp.text()
            soup = BeautifulSoup(html, "html.parser")
            main = soup.select_one("#region-main") or soup
            title_el = soup.select_one("h1.h2.mb-0")
            title = title_el.get_text(strip=True) if title_el else "Без названия"
            grade_cell = main.select_one("td.cell.c0")
            if grade_cell:
                grade = grade_cell.get_text(strip=True)
                comment_cell = main.select_one("td.cell.c1")
                comment = comment_cell.get_text(strip=True) if comment_cell else ""
                date = main.select_one("td.cell.c2").get_text(strip=True)
                teacher = main.select_one("td.cell.c3").get_text(strip=True)
                parts = [f"📘 {title}",f"✅ Оценка: {grade}"]
                if comment:
                    parts.append(f"💬 Комментарий: {comment}")
                parts += [f"📅 Дата: {date}",f"👩‍🏫 Преподаватель: {teacher}"]
                text = "\n".join(parts)
                return {"type": "text", "text": text}
            alert = main.select_one(".mod_outgrade .alert")
            if alert:
                alert_text = alert.get_text(strip=True)
                desc_el = main.select_one(".mod_outgrade .generalbox .no-overflow p")
                desc = desc_el.get_text(strip=True) if desc_el else None
                if desc:
                    text = f"📘 {title}\n📝 {desc}\nℹ️ {alert_text}"
                else:
                    text = f"📘 {title}\nℹ️ {alert_text}"
                return {"type": "text", "text": text}
            return {"type": "text", "text": f"Не удалось определить состояние оценки ({title})"}
        case "attendancernhgs":
            url = f"https://lms.ranepa.ru/mod/attendancernhgs/manage.php?id={cm_id}&brspage=brs"
            async with aiohttp.ClientSession(cookie_jar=cookie_jar) as session:
                async with session.get(url) as resp:
                    if "login/index.php" in str(resp.url):
                        return {"type": "error", "text": "Время вашего сеанса истекло. Пожалуйста, войдите снова."}
                    html = await resp.text()
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page()
                await page.set_content(html, wait_until="networkidle")
                png_bytes = await page.screenshot(full_page=True)
                await browser.close()
                png = png_bytes
            bio = BytesIO(png)
            bio.name = "page.png"
            soup = BeautifulSoup(html, "html.parser")
            hidden = soup.select(".folded")
            kt_titles = []
            kt_scores = []
            db_titles = []
            db_scores = []
            for el in hidden:
                pid = el.get("parentid")
                text = el.get_text(strip=True)
                if pid == "ktpoints":
                    if el.name == "th":
                        kt_titles.append(text)
                    elif el.name == "td":
                        kt_scores.append(text)
                elif pid == "db":
                    if el.name == "th":
                        db_titles.append(text)
                    elif el.name == "td":
                        db_scores.append(text)
            msg = "Контрольные точки:\n"
            for t, s in zip(kt_titles, kt_scores):
                msg += f"- {t} - {s}\n"
            msg += "\nДополнительные баллы:\n"
            for t, s in zip(db_titles, db_scores):
                msg += f"- {t} - {s}\n"
            return {"type": "photo+message", "photo": InputFile(bio),"message":msg}
        case "workshop" | "quiz" | "page" | "vwork" | "folder" | "video":
            async with aiohttp.ClientSession(cookie_jar=cookie_jar) as session:
                async with session.get(url) as resp:
                    if "login/index.php" in str(resp.url):
                        return {"type": "error", "text": "Время вашего сеанса истекло. Пожалуйста, войдите снова."}
                    html = await resp.text()
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page()
                await page.set_content(html, wait_until="networkidle")
                png_bytes = await page.screenshot(full_page=True)
                await browser.close()
                png = png_bytes
            bio = BytesIO(png)
            bio.name = "page.png"
            return {"type": "photo", "photo": InputFile(bio)}
        case _:
            return {"type":None,"text":f'Неверный формат команды или элемент "{cm_type}" не поддерживается'}

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Используйте /login для входа в аккаунт\nИспользуйте /grades для получения оценок\nИспользуйте /dashboard для получения дашборда")

async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = await update.message.reply_text("Авторизируюсь в аккаунт...")
    try:
        user_id = update.effective_user.id
        with open('credentials.json') as f:
            username, password = json.load(f)[str(user_id)].split(';')
        await login_to_moodle(username,password,user_id)
        conn = sqlite3.connect("sessions.db")
        cursor = conn.cursor()
        cursor.execute("SELECT cookie_jar FROM sessions WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            await update.message.reply_text("Ошибка авторизации")
            return
        cookies_json = row[0]
        cookies = json.loads(cookies_json)
        cookie_jar = aiohttp.CookieJar()
        cookie_jar.update_cookies(cookies)
        await get_sesskey(cookie_jar,user_id)
        conn = sqlite3.connect("sessions.db")
        cursor = conn.cursor()
        cursor.execute("SELECT sesskey FROM sessions WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            await update.message.reply_text("Ошибка авторизации")
            return
        else:
            await update.message.reply_text(f"Успешная авторизация в аккаунт <b>{username}</b>",parse_mode='HTML')
    finally:
        with suppress(Exception):
            await status.delete()

async def grades_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = await update.message.reply_text("Получаю оценки...")
    try:
        user_id = update.effective_user.id
        conn = sqlite3.connect("sessions.db")
        cursor = conn.cursor()
        cursor.execute("SELECT cookie_jar FROM sessions WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            await update.message.reply_text("Для использования этой команды нужно сначала войти в аккаунт /login")
            return
        cookies_json = row[0]
        cookies = json.loads(cookies_json)
        cookie_jar = aiohttp.CookieJar()
        cookie_jar.update_cookies(cookies)
        grades = await get_grades(cookie_jar)
        await update.message.reply_text(grades,parse_mode='HTML')
    finally:
            with suppress(Exception):
                await status.delete()

async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = await update.message.reply_text("Получаю дашборд...")
    try:
        user_id = update.effective_user.id
        conn = sqlite3.connect("sessions.db")
        cursor = conn.cursor()
        cursor.execute("SELECT sesskey, cookie_jar FROM sessions WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            await update.message.reply_text("Для использования этой команды нужно сначала войти в аккаунт /login")
            return
        sesskey, cookies_json = row
        cookies = json.loads(cookies_json)
        cookie_jar = aiohttp.CookieJar()
        cookie_jar.update_cookies(cookies)
        result = await get_dashboard(sesskey,cookie_jar)
        await update.message.reply_text(result,parse_mode='HTML')
    finally:
            with suppress(Exception):
                await status.delete()

async def open_course(update: Update, context: ContextTypes.DEFAULT_TYPE):
    match = re.match(r"^/course_(\d+)$", update.message.text)
    if match:
        course_id = match.group(1)
        status = await update.message.reply_text("Получаю курс...")
        try:
            user_id = update.effective_user.id
            conn = sqlite3.connect("sessions.db")
            cursor = conn.cursor()
            cursor.execute("SELECT sesskey, cookie_jar FROM sessions WHERE user_id=?", (user_id,))
            row = cursor.fetchone()
            conn.close()
            if not row:
                await update.message.reply_text("Для использования этой команды нужно сначала войти в аккаунт /login")
                return
            sesskey, cookies_json = row
            cookies = json.loads(cookies_json)
            cookie_jar = aiohttp.CookieJar()
            cookie_jar.update_cookies(cookies)
            result = await get_course(sesskey,cookie_jar,course_id)
            await send_html_chunks(update, result)
        finally:
            with suppress(Exception):
                await status.delete()
    else:
        await update.message.reply_text("Неверный формат команды")

async def open_cm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    match = re.match(r"^/cm_([a-zA-Z]+)_(\d+)$", update.message.text)
    if match:
        cm_type = match.group(1)
        cm_id = match.group(2)
        status = await update.message.reply_text("Получаю элемент курса...")
        try:
            user_id = update.effective_user.id
            conn = sqlite3.connect("sessions.db")
            cursor = conn.cursor()
            cursor.execute("SELECT cookie_jar FROM sessions WHERE user_id=?", (user_id,))
            row = cursor.fetchone()
            conn.close()
            if not row:
                await update.message.reply_text("Для использования этой команды нужно сначала войти в аккаунт /login")
                return
            cookies_json = row[0]
            cookies = json.loads(cookies_json)
            cookie_jar = aiohttp.CookieJar()
            cookie_jar.update_cookies(cookies)
            result = await get_cm(cookie_jar,cm_id,cm_type)
            if result.get("type") == "file":
                await update.message.reply_document(document=result.get("file"))
            elif result.get("type") == "photo":
                await update.message.reply_photo(photo=result.get("photo"))
            elif result.get("type") == "photo+message":
                await update.message.reply_photo(photo=result.get("photo"), caption = result.get("message"))
            else:
                await update.message.reply_text(result.get("text"))
        finally:
            with suppress(Exception):
                await status.delete()
    else:
        await update.message.reply_text("Неверный формат команды")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("grades", grades_command))
    app.add_handler(CommandHandler("dashboard",dashboard_command))
    app.add_handler(CommandHandler("login",login_command))
    app.add_handler(MessageHandler(filters.Regex(r"^/course_\d+$"), open_course))
    app.add_handler(MessageHandler(filters.Regex(r"^/cm_[a-zA-Z]+_\d+$"), open_cm))
    app.run_polling()

if __name__ == "__main__":
    main()