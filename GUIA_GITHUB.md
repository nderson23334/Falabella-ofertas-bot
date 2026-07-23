# Configurar gratis con la laptop apagada

## 1. Crear cuenta y repositorio

1. Entra a GitHub y crea una cuenta.
2. Presiona `+` y luego **New repository**.
3. Nombre: `falabella-ofertas-bot`.
4. Elige **Public**.
5. Presiona **Create repository**.

El repositorio será público, pero el token se guardará como secreto.

## 2. Subir el proyecto

1. Presiona **uploading an existing file**.
2. Sube todos los archivos y carpetas.
3. No subas `.env`.
4. Verifica que exista `.github/workflows/falabella.yml`.
5. Presiona **Commit changes**.

## 3. Guardar secretos

Abre:

**Settings → Secrets and variables → Actions → New repository secret**

Crea:

- `TELEGRAM_BOT_TOKEN`: tu token actual.
- `TELEGRAM_CHAT_ID`: el número obtenido con `/miid`.

## 4. Permitir guardar historial

Abre:

**Settings → Actions → General → Workflow permissions**

Marca **Read and write permissions** y guarda.

## 5. Primera prueba

1. Ve a **Actions**.
2. Abre **Buscar ofertas Falabella**.
3. Presiona **Run workflow**.
4. Espera una marca verde.
5. Revisa Telegram.

Después revisará automáticamente cada 20 minutos aunque la laptop esté apagada.
