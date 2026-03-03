# ForensicVision 

Уеб платформа за **криминалистичен анализ на документи** (PDF - изображения) и изображения, с pipeline анализи и отчети.

**Технологии:** Django 5 · DRF · PostgreSQL · Celery · Redis · Docker Compose · Vanilla CSS

---

## Структура на проекта

```
spatial-kepler/
├── forensicvision/       # Django project (settings, urls, celery)
├── core/                 # Case, Evidence, EvidencePage, AnalysisJob, Artifact, ShareLink, Comment
├── api/                  # DRF serializers, views, permissions, urls
├── analysis/             # Pipeline registry + Celery tasks
├── audit/                # AuditLog модел
├── reporting/            # Скелет (бъдеща функционалност)
├── templates/            # HTML шаблони (BG)
├── static/css/style.css  # Vanilla CSS
├── docker-compose.yml
├── Dockerfile
└── .env.example
```

---

## Бързо стартиране (Docker)

### 1. Клонирай и конфигурирай

```bash
git clone <repo>
cd spatial-kepler
cp .env.example .env
# Редактирай .env – смени SECRET_KEY в production!
```

### 2. Изгради и стартирай

```bash
docker compose build
docker compose up -d
```

### 3. Мигрирай базата + seed

```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py seed_demo
```

### 4. Създай superuser (admin panel)

```bash
docker compose exec web python manage.py createsuperuser
```

### 5. Отвори

| URL | Описание |
|---|---|
| `http://localhost:8000/` | Табло (изисква login) |
| `http://localhost:8000/login/` | Форма за вход |
| `http://localhost:8000/cases/` | Списък кейсове |
| `http://localhost:8000/admin/` | Django Admin |
| `http://localhost:8000/api/` | DRF API Browser |

**Demo вход:** `demo` / `demo1234`

---

## API – Примерни curl команди

### Вземи Token

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{"username":"demo","password":"demo1234"}' | python -c "import sys,json; print(json.load(sys.stdin)['token'])")

echo "Token: $TOKEN"
```

### Списък кейсове

```bash
curl -H "Authorization: Token $TOKEN" http://localhost:8000/api/cases/
```

### Създай кейс

```bash
curl -H "Authorization: Token $TOKEN" \
  -X POST http://localhost:8000/api/cases/ \
  -H "Content-Type: application/json" \
  -d '{"title":"Тест кейс","description":"Описание","status":"draft","tags":["тест"]}'
```

### Качи доказателство (PDF)

```bash
curl -H "Authorization: Token $TOKEN" \
  -X POST http://localhost:8000/api/evidence/ \
  -F "case=1" \
  -F "file=@/path/to/document.pdf"
```

> Celery worker автоматично ще рендерира страниците на PDF-а.

### Провери страниците

```bash
curl -H "Authorization: Token $TOKEN" http://localhost:8000/api/evidence/1/pages/
```

### Стартирай анализ

```bash
curl -H "Authorization: Token $TOKEN" \
  -X POST http://localhost:8000/api/analysis/jobs/ \
  -H "Content-Type: application/json" \
  -d '{"case":1,"evidence":1,"pipeline_name":"general_scan"}'
```

**Налични pipeline-и:** `general_scan`, `layout_consistency`, `compare_reference`, `handwriting_compare`

### Провери статус на анализ

```bash
curl -H "Authorization: Token $TOKEN" http://localhost:8000/api/analysis/jobs/1/
```

### Сподели кейс (preглед)

```bash
# Създай viewer link
curl -H "Authorization: Token $TOKEN" \
  -X POST http://localhost:8000/api/cases/1/share-links/ \
  -H "Content-Type: application/json" \
  -d '{"role":"viewer"}'

# Отмени линк (owner only)
curl -H "Authorization: Token $TOKEN" \
  -X POST http://localhost:8000/api/share-links/<UUID>/revoke/
```

### Коментари

```bash
# Виж коментари
curl -H "Authorization: Token $TOKEN" http://localhost:8000/api/cases/1/comments/

# Изпрати коментар
curl -H "Authorization: Token $TOKEN" \
  -X POST http://localhost:8000/api/cases/1/comments/ \
  -H "Content-Type: application/json" \
  -d '{"text":"Забелязан е проблем с отпечатък на стр. 3."}'
```

---

## Разрешения (кратко)

| Действие | Viewer | Editor | Owner | Admin |
|---|:---:|:---:|:---:|:---:|
| Чете case/evidence/jobs | ✓ | ✓ | ✓ | ✓ |
| Качва evidence         | ✗ | ✓ | ✓ | ✓ |
| Стартира анализ        | ✗ | ✓ | ✓ | ✓ |
| Коментира              | ✓ | ✓ | ✓ | ✓ |
| Viewer share links     | ✗ | ✓ | ✓ | ✓ |
| Editor share links     | ✗ | ✗ | ✓ | ✓ |
| Отменя линкове         | ✗ | ✗ | ✓ | ✓ |

---

## Полезни команди

```bash
# Логове на worker
docker compose logs -f worker

# Django shell
docker compose exec web python manage.py shell

# Рестартирай само worker
docker compose restart worker

# Спри всичко
docker compose down

# Изтрий данните (внимание!)
docker compose down -v
```



