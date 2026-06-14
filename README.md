<p align="center">
  <a href="#%E2%84%B9%EF%B8%8F-%D0%BE%D0%BF%D0%B8%D1%81%D0%B0%D0%BD%D0%B8%D0%B5">
    <img alt="Logo" width="128" src="https://user-images.githubusercontent.com/20641837/174094285-6e32eb04-7feb-4a60-bddf-5a0fde5dba4d.png"/>
  </a>
</p>
<h1 align="center">Parser2GIS</h1>

## Server-side Stage 1

The repository includes `stage1_2gis`, a PostgreSQL-backed acquisition worker
for Debian 13. It uses Pydantic 2 and a Playwright-managed Chromium, stores
every catalog item immediately, and claims jobs through PostgreSQL.

```bash
python -m stage1_2gis migrate
python -m stage1_2gis create-run
python -m stage1_2gis create-job \
  --run-id <uuid> \
  --city-key moscow \
  --query-key dentistry \
  --url "https://2gis.ru/moscow/search/стоматологии" \
  --max-records 100
python -m stage1_2gis browser-worker
```

See `deploy/README.md` for native systemd deployment and the browser update
procedure. The original desktop source remains in the repository for migration
reference, but it is not included in the production Stage 1 wheel.

<p align="center">
  <a href="https://github.com/interlark/parser-2gis/actions/workflows/tests.yml"><img src="https://github.com/interlark/parser-2gis/actions/workflows/tests.yml/badge.svg" alt="Tests"/></a>
  <a href="https://pypi.org/project/parser-2gis"><img src="https://badgen.net/pypi/v/parser-2gis" alt="PyPi version"/></a>
  <a href="https://pypi.org/project/parser-2gis"><img src="https://badgen.net/pypi/python/parser-2gis" alt="Supported Python versions"/></a>
  <a href="https://github.com/interlark/parser-2gis/releases"><img src="https://img.shields.io/github/downloads/interlark/parser-2gis/total.svg" alt="Downloads"/></a>
</p>

**Parser2GIS** - парсер сайта [2GIS](https://2gis.ru/) с помощью браузера [Google Chrome](https://google.com/chrome).

<img alt="Screenshot" src="https://user-images.githubusercontent.com/20641837/174098241-7c0874aa-e70d-4978-86dc-7fd90af44603.png"/>

## ℹ️ Описание

Парсер для автоматического сбора базы адресов и контактов предприятий, которые работают на территории
России <img width="18px" src="https://user-images.githubusercontent.com/20641837/183511175-3d47f0f0-4e3f-45d2-8495-95d0612a8a8c.svg"/>, Казахстана <img width="18px" src="https://user-images.githubusercontent.com/20641837/183511625-20420aef-59c3-426d-a112-654d2caf0dda.svg"/>, Беларуси <img width="18px" src="https://user-images.githubusercontent.com/20641837/183511940-ce088ad1-d97f-4fa1-849a-9b887ad481c5.svg"/>,
Азербайджана <img width="18px" src="https://user-images.githubusercontent.com/20641837/183512176-1f6795a1-ceac-4865-a29f-b5720ce5115e.svg"/>, Киргизии <img width="18px" src="https://user-images.githubusercontent.com/20641837/183512234-286ca403-5194-4a6d-a59e-59201140078a.svg"/>, Узбекистана <img width="18px" src="https://user-images.githubusercontent.com/20641837/183512333-7ec1f36d-07fe-450d-b6f1-eed59a3b69c8.svg"/>, Чехии <img width="18px" src="https://user-images.githubusercontent.com/20641837/183512458-5a5d9531-a8f0-4624-99da-7069cde84926.svg"/>, Египта <img width="18px" src="https://user-images.githubusercontent.com/20641837/183512581-71fa2106-8cc1-43cc-a680-b3ff420acb8a.svg"/>, Италии <img width="18px" src="https://user-images.githubusercontent.com/20641837/183512763-0b438e5b-3ff0-4717-a826-0baac9207167.svg"/>, Саудовской Аравии <img width="18px" src="https://user-images.githubusercontent.com/20641837/183512980-427a985a-df1b-42c8-90bb-2c61692b6654.svg"/>, Кипра <img width="18px" src="https://user-images.githubusercontent.com/20641837/183513128-4367d2b1-feb9-4efe-bc57-73a15d178ef2.svg"/>, Объединенных Арабских Эмиратов <img width="18px" src="https://user-images.githubusercontent.com/20641837/183513374-9afef8c7-923e-4a18-9cd8-c69645b99377.svg"/>, Чили <img width="18px" src="https://user-images.githubusercontent.com/20641837/183513576-7209ce90-a04a-4258-9832-ef210198c3c4.svg"/>, Катара <img width="18px" src="https://user-images.githubusercontent.com/20641837/183513757-143ee2bf-b66c-4766-bbe1-db896a33eac1.svg"/>, Омана <img width="18px" src="https://user-images.githubusercontent.com/20641837/183513865-27509b74-b08f-4d92-b83b-a0d3aaabe155.svg"/>, Бахрейна <img width="18px" src="https://user-images.githubusercontent.com/20641837/183514076-3b6c9496-7c95-4452-8ee1-8723d98f876d.svg"/>, Кувейта <img width="18px" src="https://user-images.githubusercontent.com/20641837/183514240-7eff8632-5cd2-46ac-bed4-e483bb2df5f0.svg"/>.

## ✨ Особенности
- 💰 Абсолютно бесплатный
- 🤖 Успешно обходит анти-бот блокировки на территории РФ
- 🖥️ Работает под Windows, Linux и MacOS
- 📄 Три выходных формата: CSV таблица, XLSX таблица и JSON список
- 🔗 Наличие генератора ссылок по городам и рубрикам

## 🚀 Установка
> Для работы парсера необходимо установить браузер [Google Chrome](https://google.com/chrome).

### Установка одним файлом

  Скачать [релиз](https://github.com/interlark/parser-2gis/releases/latest).

### Установка из PyPI
  ```bash
  # CLI
  pip install parser-2gis
  # CLI + GUI
  pip install parser-2gis[gui]
  ```

## 📖 Документация
Описание работы доступно на [вики](https://github.com/interlark/parser-2gis/wiki).

## 👍 Поддержать проект
<a href="https://yoomoney.ru/to/4100118362270186" target="_blank">
  <img alt="Yoomoney Donate" src="https://github.com/interlark/parser-2gis/assets/20641837/e875e948-0d69-4ed5-804c-8a1736ab0c9d" width="150">
</a>
