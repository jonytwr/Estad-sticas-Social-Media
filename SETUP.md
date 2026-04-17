# Guía de configuración — @defensagob Dashboard

Tiempo estimado de configuración: **20-30 minutos**

---

## Paso 1 — Crear el bot de Telegram

1. Abre Telegram y busca **@BotFather**
2. Escríbele: `/newbot`
3. Pon el nombre que quieras (p. ej. "Defensagob Stats")
4. Pon el username del bot (debe acabar en `bot`, p. ej. `defensagob_stats_bot`)
5. BotFather te dará un **token** con esta pinta: `1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ`
6. Guarda ese token — lo necesitarás en el Paso 3

**Obtener tu Chat ID:**
1. Busca tu nuevo bot en Telegram y escríbele `/start`
2. Luego ve a: `https://api.telegram.org/bot<TU_TOKEN>/getUpdates`
   (sustituye `<TU_TOKEN>` por el token que te dio BotFather)
3. En el JSON que aparece, busca `"chat":{"id":XXXXXXX}` — ese número es tu Chat ID

---

## Paso 2 — Crear cuenta en Railway

1. Ve a **[railway.app](https://railway.app)** y regístrate (puedes usar GitHub)
2. El plan gratuito incluye 500 horas/mes y 512MB RAM — suficiente para esto

---

## Paso 3 — Subir el código a Railway

**Opción A: Desde GitHub (recomendado)**
1. Crea un repositorio en GitHub (puede ser privado)
2. Sube todos los archivos de esta carpeta al repositorio
3. En Railway: **New Project → Deploy from GitHub repo → selecciona tu repo**

**Opción B: Subida directa**
1. En Railway: **New Project → Deploy from local folder**
2. Selecciona esta carpeta

---

## Paso 4 — Añadir base de datos PostgreSQL

1. En tu proyecto de Railway, haz clic en **+ Add Service → Database → PostgreSQL**
2. Railway creará la base de datos automáticamente y añadirá `DATABASE_URL` a las variables de entorno de forma automática ✅

---

## Paso 5 — Configurar las variables de entorno

En Railway, ve a tu servicio → **Variables** → añade estas variables:

| Variable | Valor |
|---|---|
| `ANTHROPIC_API_KEY` | Tu clave de la API de Anthropic (console.anthropic.com) |
| `TELEGRAM_BOT_TOKEN` | El token del Paso 1 |
| `TELEGRAM_CHAT_ID` | Tu Chat ID del Paso 1 |
| `TWITTER_HANDLE` | `Defensagob` |
| `APP_URL` | La URL de tu app en Railway (la ves en Settings → Domain) |
| `ADMIN_SECRET` | Una contraseña inventada para uso manual |
| `SCRAPE_HOUR` | `8` (hora a la que se ejecuta el informe diario) |
| `SCRAPE_MINUTE` | `0` |

> **Nota:** `DATABASE_URL` ya la habrá puesto Railway automáticamente en el Paso 4.

---

## Paso 6 — Desplegar

1. Railway debería desplegarse automáticamente tras configurar las variables
2. Si no, haz clic en **Deploy** en tu servicio
3. Espera ~2-3 minutos (instala Playwright + dependencias)
4. Verifica que funciona yendo a: `https://TU-APP.up.railway.app/health`
   Deberías ver: `{"status":"ok","bot_active":true}`

---

## Paso 7 — Importar tus archivos históricos

Para migrar todos tus archivos .md actuales a la base de datos:

1. En Railway, ve a tu servicio → **Deploy → Settings → start command** y cámbialo temporalmente a:
   ```
   python parser.py /app/informes
   ```
   O bien, sube los archivos al repo en una carpeta `informes/` y ejecuta el parser como proceso independiente.

**Alternativa más sencilla:** Sube los archivos .md uno a uno usando el endpoint de importación (próximamente) o directamente desde tu ordenador si Railway tiene acceso al volumen.

> Si no tienes muchos archivos históricos, también puedes empezar desde cero. Los informes se irán acumulando a partir de hoy.

---

## Flujo diario una vez configurado

```
08:00h → Railway ejecuta el scraper (sin necesitar Chrome abierto)
          └→ Playwright accede a Nitter en modo headless
          └→ Claude API genera el análisis completo
          └→ Todo se guarda en PostgreSQL

08:02h → Tu bot de Telegram te envía un mensaje:
          "📋 Informe del 15 de abril listo — 4 posts pendientes de autor"

          Para cada post:
          ┌─────────────────────────────────┐
          │ 🐦 Post 1 · 09:00h              │
          │ "Zapadores del Ejército de..."  │
          │ ¿Quién lo publicó?              │
          │ [Jony] [Fernando] [Vicky]       │
          │ [María José] [Cecilia] [Luís]   │
          │ [Elena] [Amparo]                │
          └─────────────────────────────────┘

          Tú pulsas el nombre → siguiente post → ...

08:05h → "🎉 ¡Todos los autores asignados!"
          https://TU-APP.up.railway.app

          Dashboard actualizado con todos los datos ✅
```

---

## Acceso al dashboard

- **Desde cualquier dispositivo:** `https://TU-APP.up.railway.app`
- **Desde el móvil:** misma URL, el dashboard es responsive
- El dashboard muestra: resumen diario, histórico, estadísticas por autor,
  alertas de usuarios reincidentes, análisis de formatos y franjas horarias

---

## Comandos del bot de Telegram

| Comando | Qué hace |
|---|---|
| `/start` | Muestra tu Chat ID y los comandos disponibles |
| `/estado` | Resumen del informe más reciente |
| `/pendientes` | Inicia la asignación de autores pendientes |

---

## Disparar el informe manualmente

Si un día el scraper falla o quieres regenerar:

```
GET https://TU-APP.up.railway.app/api/trigger-daily?secret=TU_ADMIN_SECRET
```

---

## Resolución de problemas frecuentes

**El bot no responde:**
- Comprueba que `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` son correctos
- Ve a Railway → Logs y busca errores de Telegram

**Nitter no carga:**
- Nitter puede tener caídas. El scraper prueba 4 instancias automáticamente.
- Si todas fallan, el log mostrará "Todas las instancias de Nitter fallaron"
- Puedes añadir nuevas instancias en `scraper.py → NITTER_INSTANCES`

**El análisis de Claude no se genera:**
- Comprueba que `ANTHROPIC_API_KEY` es válida
- Revisa si tienes saldo en console.anthropic.com

**Railway pone la app en sleep:**
- El plan gratuito no pone en sleep los servicios con tráfico, pero si no hay
  tráfico durante mucho tiempo puede ocurrir. Actualiza a Hobby ($5/mes) si
  necesitas garantía de disponibilidad 24/7.
