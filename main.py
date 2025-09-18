import aiohttp
import asyncio
import json
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

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
            result = ["<b>📊 Ваши оценки:</b>\n"]
            result.extend(courses)
            result.append(f"\n📈 <b>Всего курсов:</b> {len(courses)}")
            return "\n".join(result)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Используй /grades для получения оценок")

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

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("grades", grades_command))
    app.run_polling()

if __name__ == "__main__":
    main()