import requests
from requests.auth import HTTPBasicAuth
import json

# Конфигурация
CONFLUENCE_URL = 'https://your-domain.atlassian.net/wiki/rest/api/content'
SPACE_KEY = 'YOUR_SPACE_KEY'  # Замените на ключ вашего пространства в Confluence
USERNAME = 'your-email@example.com'  # Ваш email для доступа к Confluence
API_TOKEN = 'your-api-token'  # Токен для авторизации (его можно получить в настройках Atlassian)

# Параметры запроса
headers = {
    'Content-Type': 'application/json',
    'Accept': 'application/json'
}


# Функция для получения всех страниц
def get_pages(space_key, start=0, limit=50):
    url = f'{CONFLUENCE_URL}?spaceKey={space_key}&start={start}&limit={limit}'
    response = requests.get(url, headers=headers, auth=HTTPBasicAuth(USERNAME, API_TOKEN))

    if response.status_code == 200:
        return response.json()
    else:
        print(f"Ошибка при получении данных: {response.status_code}")
        return None


# Функция для перебора всех страниц
def get_all_pages(space_key):
    start = 0
    limit = 50  # Количество страниц на одну страницу ответа API
    pages = []

    while True:
        data = get_pages(space_key, start, limit)
        if data and 'results' in data:
            pages.extend(data['results'])
            start += limit  # Увеличиваем offset для следующей порции данных
            if len(data['results']) < limit:
                break  # Если страниц меньше, чем limit, значит мы дошли до последней страницы
        else:
            print("Не удалось получить страницы.")
            break

    return pages


# Основной блок
if __name__ == '__main__':
    all_pages = get_all_pages(SPACE_KEY)
    if all_pages:
        print(f"Найдено {len(all_pages)} страниц в пространстве '{SPACE_KEY}':")
        for page in all_pages:
            title = page['title']
            id = page['id']
            print(f"Страница: {title} (ID: {id})")
    else:
        print("Нет страниц для отображения.")