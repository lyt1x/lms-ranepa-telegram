import aiohttp
import json
import re
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

with open('token.txt') as f:
    BOT_TOKEN = f.read().strip()

async def login_to_moodle(username, password):
    async with aiohttp.ClientSession() as session:
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
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        async with session.post(login_url,data=payload,headers=headers,allow_redirects=True) as response:
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
            result = "<b>📚 Курсы FIO:</b>\n" #доделать получение ФИО
            for course in courses:
                result += f"{course.get('fullname')}\n➡️ /course_{course.get('id')}\n\n"
            return result
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
    await update.message.reply_text("Получаю дешбоард...")
    with open('credentials.json') as f:
        username, password = json.load(f)["1"].split(';')
    cookie_jar = await login_to_moodle(username, password)
    if cookie_jar:
        sesskey = await get_sesskey(cookie_jar)
        result = await get_dashboard(sesskey,cookie_jar)
        await update.message.reply_text(result,parse_mode='HTML')
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
        await update.message.reply_text(f"Открыл курс ID: {course_id}")
    else:
        await update.message.reply_text("Неверный формат команды")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("grades", grades_command))
    app.add_handler(CommandHandler("dashboard",dashboard_command))
    app.add_handler(MessageHandler(filters.Regex(r"^/course_\d+$"), open_course))
    app.run_polling()

if __name__ == "__main__":
    main()