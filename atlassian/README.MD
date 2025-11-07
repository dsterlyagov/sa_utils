export CONF_URL="https://confluence.sberbank.ru"
export CONF_USER="user.name"              # при PAT можно оставить пустым или указать любой
export CONF_PASS="password_or_pat_token"  # пароль / API token / PAT
export CONF_PAGE_ID="21609790602"
export NODE_SCRIPT="./find-release-branches.mjs"

python3 confluence_release_update.py

# (рекомендуется) без глобальной установки пакетов:
# скрипт сам попробует `npx -y tsx` → `npx -y ts-node --transpile-only` → `node --loader ts-node/esm`

export NODE_SCRIPT="./build-meta-from-zod.ts"
export OUTPUT_DIR="./output"
export OUTPUT_FILENAME="widget-meta.json"

python3 run_build_meta_and_validate.py


# вариант 1: разово через npx (ничего ставить не нужно) — уже используется скриптом
npx -y tsx --help

# вариант 2: локально в проект
npm i -D tsx
npx tsx build-meta-from-zod.ts

# вариант 3: глобально
npm i -g tsx
tsx build-meta-from-zod.ts

