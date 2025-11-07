import requests
from requests.auth import HTTPBasicAuth
from bs4 import BeautifulSoup
import re

# Конфигурация
CONFLUENCE_URL = 'https://your-domain.atlassian.net/wiki/rest/api/content'
PAGE_ID = 'YOUR_PAGE_ID'  # Замените на ID страницы, которую вы хотите очистить
USERNAME = 'your-email@example.com'  # Ваш email для доступа к Confluence
API_TOKEN = 'your-api-token'  # Токен для авторизации (его можно получить в настройках Atlassian)

# Параметры запроса
headers = {
    'Content-Type': 'application/json',
    'Accept': 'application/json'
}


# Функция для получения данных страницы
def get_page_data(page_id):
    url = f'{CONFLUENCE_URL}/{page_id}?expand=body.storage'
    response = requests.get(url, headers=headers, auth=HTTPBasicAuth(USERNAME, API_TOKEN))

    if response.status_code == 200:
        return response.json()
    else:
        print(f"Ошибка при получении данных страницы: {response.status_code}")
        return None


# Функция для очистки HTML-разметки
def clean_html(content):
    # Используем BeautifulSoup для удаления HTML
    soup = BeautifulSoup(content, 'html.parser')
    return soup.get_text()


# Функция для удаления диаграмм PlantUML
def remove_plantuml(content):
    # Регулярное выражение для поиска PlantUML диаграмм
    plantuml_pattern = r'\{plantuml.*?\}.*?\{plantuml\}'  # Это ищет блоки с диаграммами PlantUML
    cleaned_content = re.sub(plantuml_pattern, '', content, flags=re.DOTALL)
    return cleaned_content

''''
Для очистки данных, полученных со страницы Confluence, вам нужно будет удалить HTML-разметку и удалить диаграммы PlantUML.
 Чтобы это сделать, мы можем воспользоваться библиотеками BeautifulSoup для парсинга HTML и удаления разметки, а
  также регулярными выражениями для удаления диаграмм PlantUML.
'''
# Основной блок
if __name__ == '__main__':
    page_data = get_page_data(PAGE_ID)
    if page_data:
        # Извлекаем тело страницы в формате Storage (с HTML)
        page_content = page_data['body']['storage']['value']

        # Очищаем HTML-разметку
        clean_content = clean_html(page_content)

        # Удаляем диаграммы PlantUML
        final_content = remove_plantuml(clean_content)

        # Печатаем очищенное содержимое
        print("Очищенное содержимое страницы:")
        print(final_content)
