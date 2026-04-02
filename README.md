# 🤖 Ultron — Discord ↔ Redmine + LLM

Bot de **Discord** que conecta **Redmine** con un LLM **compatible con OpenAI** (OpenAI, OpenRouter, Ollama local, etc.): resúmenes y notas pulidas por comando, e informes programados para tickets abandonados o nuevos sin actividad.

[`.env.example`](.env.example) · [`config.example.yaml`](config.example.yaml)

---

## Demo

<!-- Sustituye por tu vídeo: YouTube, Loom, o un GIF. Ejemplo:
[![Demo](https://img.youtube.com/vi/TU_VIDEO_ID/maxresdefault.jpg)](https://www.youtube.com/watch?v=TU_VIDEO_ID)
-->

*Próximamente: vídeo o captura aquí.*

---

## Por qué Ultron

- **🔗 Discord + Redmine** — Slash commands sin salir del servidor.
- **🧠 Cualquier LLM OpenAI-compatible** — Un solo `.env` o cadena `llm_chain` en YAML (ver ejemplo).
- **⏰ Informes** — Tickets viejos o “nuevos” sin movimiento, según horarios en config.
- **🔐 Lista blanca** — `/summary`, `/note`, `/ping` y `/status` solo para usuarios aprobados; `/token` + `/approve` para dar de alta.

---

## Requisitos

| | |
|--|--|
| Python | **3.11+** |
| Discord | App con bot + token |
| Redmine | REST API + API key |
| LLM | Endpoint `/v1/chat/completions` |

---

## Inicio rápido

```bash
git clone <url-del-repositorio>
cd ultron-redmine
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env          # rellena credenciales
cp config.example.yaml config.yaml   # ajusta canal de informes, zona horaria, etc.
python -m ultron              # o: ultron
```

1. **Variables:** copia [`.env.example`](.env.example) → `.env` y completa lo marcado como obligatorio (Discord, Redmine, LLM). El propio archivo comenta el resto.
2. **Config YAML:** copia [`config.example.yaml`](config.example.yaml) → `config.yaml`. Ahí están comentadas `llm_chain`, informes (`reports`, `schedules`), textos de Discord y logging.
3. **Discord:** invita el bot con `applications.commands` y permiso para escribir en el canal de informes si usas reportes automáticos.

Desarrollo con comandos que actualizan al instante: define `DISCORD_GUILD_ID` en `.env`.

---

## Comandos principales

| Comando | Quién |
|--------|--------|
| `/help` | Todos |
| `/ping` | Whitelist — responde `Pong` (visible en canal si no es efímero) |
| `/status` | Whitelist — marcador de sitio (visibilidad según `discord.ephemeral_default`) |
| `/summary`, `/note` | Solo usuarios en whitelist |
| `/token` (DM) | Solicitar código de alta |
| `/approve`, `/remove`, `/show_config` | Admins (`DISCORD_ADMIN_IDS` o `admins.json`); `/show_config` muestra ajustes no secretos (solo tú, efímero) |

Flujo típico de acceso: el usuario hace **`/token` en DM** → un admin usa **`/approve`** con ese token (o en el host: `ultron add token '<token>'`).

---

## Docker (opcional)

```bash
docker build -t ultron .
docker run --rm --env-file .env -v "$(pwd)/config.yaml:/app/config.yaml:ro" ultron
```

---

## Seguridad (resumen)

No subas **`.env`**, **`config.yaml`** ni el directorio de estado (`whitelist`, admins, tokens pendientes). Evita `logging.log_read_messages: true` en producción si los logs no son solo locales.

---

## CI y despliegue (rama `prod`)

En **GitHub → Settings → Secrets and variables → Actions → New repository secret**, configura:

| Secreto | Uso |
|--------|-----|
| `SSH_PRIVATE_KEY` | Clave **privada** de despliegue (ed25519 o RSA), una sola línea por bloque PEM. |
| `SSH_KNOWN_HOSTS` | Salida de `ssh-keyscan amvara4` (o IP del servidor) para verificación del host. |
| `DEPLOY_USER` | Usuario SSH (p. ej. `luipy`). |
| `DEPLOY_HOST` | Host al que conectar (p. ej. `amvara4` si está en `~/.ssh/config` del runner… en Actions suele ser **FQDN o IP**). |

En el **servidor** (`amvara4`), añade la clave **pública** correspondiente a `~/.ssh/authorized_keys` del usuario de despliegue. Restringe la clave si quieres (`command=`, `no-port-forwarding`, etc.).

El workflow **`.github/workflows/ci.yml`** hace: **pytest** → **build Docker** → en **push a `prod`**, `rsync` a `/home/luipy/ultron/` (excluye `.git`, `.env`, `data`) y `pip install -e .` en el `venv` del servidor. **Reinicia el bot** tras el despliegue si lo ejecutas con systemd o similar (no está automatizado para no exigir `sudo` sin configuración previa).

---

## Licencia

Úsalo y adáptalo para tu equipo.
