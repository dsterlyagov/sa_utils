1) Подготовка
кладёшь рядом файл .env с доступами к Confluence:
conf_url=https://confluence.your.company
conf_user=you@example.com
conf_pass=your_api_token_or_password
у тебя есть:
find_published_widgets_hardened.py — проверяет стенды и отдаёт JSON,
base.csv|xlsx|json — твоя базовая таблица с колонкой widget (если её нет — см. п. 6.2).
2) Один вызов — весь пайплайн
Запускаешь widgets_confluence_pipeline.py одним из двух способов.
Вариант A — запуск finder внутри
Скрипт читает .env → получает conf_url, conf_user, conf_pass.
Выполняет команду из --finder-cmd (например: python find_published_widgets_hardened.py --env DEV --env IFT).
Если finder сам пишет файл по --widgets-json-out, используем его.
Если файл не создан, скрипт берёт stdout finder’а и сохраняет в --widgets-json-out.
Если ни файла, ни валидного JSON в stdout — падает с понятной ошибкой.
Читает полученный published-widgets.json.
Извлекает версии:
version_DEV = максимальный releaseVersion из массива DEV.
version_IFT = все releaseVersion из IFT, уникальные, по возрастанию, одной строкой (например "21, 24, 40, 55, 56").
Загружает базовую таблицу --base:
поддерживает CSV/TSV/XLSX/JSON.
Мерджит по widget:
6.1 base ∪ версии → итоговый DataFrame.
6.2 Если --base не передан или файл пустой, скрипт всё равно опубликует таблицу хотя бы с колонками widget, version_DEV, version_IFT (соберёт widget из JSON).
Рендерит HTML-таблицу.
Подключается к Confluence по REST:
получает текущую версию страницы --page-id,
инкрементит версию,
заменяет тело страницы на сгенерированную HTML-таблицу.
Печатает краткое “OK” с числом строк/колонок.
Вариант B — finder уже запускался
Шаги те же, но:
пропускаем запуск finder,
сразу читаем JSON из --widgets-json,
дальше — пункты 4 → 9.
3) Что делает скрипт публикации (если запускаешь его отдельно)
confluence_save_table_fixed_patched.py — это “публикователь”:
читает .env,
читает --widgets-json,
строит version_DEV и version_IFT по правилам выше,
подмешивает их к твоему --base по widget,
рендерит HTML,
обновляет страницу Confluence (--page-id).
4) Формат и доп. нюансы
Ожидаемый JSON — список записей вида:
{ "widget": "имя", "DEV": [{"releaseVersion": 24}, ...], "IFT": [{"releaseVersion": 21}, ...] }
Если для DEV или IFT массив пустой — соответствующая колонка будет None/пусто.
Порядок значений в version_IFT — всегда отсортирован по возрастанию, без дублей.
Если Confluence вернул 4xx/5xx — в лог летит понятная ошибка (код + начало текста ответа).
5) Быстрые команды
Пайплайн с запуском finder
python widgets_confluence_pipeline.py \
  --finder-cmd "python /mnt/data/find_published_widgets_hardened.py --env DEV --env IFT" \
  --widgets-json-out /mnt/data/published-widgets.json \
  --base /path/to/base.csv \
  --page-id 123456 \
  --env-file /path/to/.env
Пайплайн с готовым JSON
python widgets_confluence_pipeline.py \
  --widgets-json /mnt/data/published-widgets.json \
  --base /path/to/base.csv \
  --page-id 123456 \
  --env-file /path/to/.env
Только публикация (без запуска finder)
python confluence_save_table_fixed_patched.py \
  --widgets-json /mnt/data/published-widgets.json \
  --base /path/to/base.csv \
  --page-id 123456 \
  --env-file /path/to/.env