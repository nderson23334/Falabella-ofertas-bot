from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

from scanner import Scanner, State, load_config, money, select_alerts

load_dotenv()

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

# Nunca mostrar las URL internas de Telegram, porque contienen el token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


async def send_telegram(
    token: str,
    chat_id: str,
    text: str,
    url: str | None = None,
    image_url: str | None = None,
    attempts: int = 4,
) -> None:
    reply_markup = None
    if url:
        reply_markup = json.dumps(
            {"inline_keyboard": [[{"text": "🛒 Ver oferta", "url": url}]]}
        )

    # Telegram limita el pie de foto a 1024 caracteres.
    use_photo = bool(image_url) and len(text) <= 1000
    method = "sendPhoto" if use_photo else "sendMessage"
    payload = {
        "chat_id": chat_id,
        "parse_mode": "HTML",
    }
    if use_photo:
        payload["photo"] = image_url
        payload["caption"] = text
    else:
        payload["text"] = text
        payload["disable_web_page_preview"] = False
    if reply_markup:
        payload["reply_markup"] = reply_markup

    last_status: int | None = None

    for attempt in range(1, attempts + 1):
        try:
            timeout = httpx.Timeout(
                connect=20.0,
                read=35.0,
                write=20.0,
                pool=20.0,
            )
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
            ) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{token}/{method}",
                    data=payload,
                )
                last_status = response.status_code

                # Algunas imágenes no son aceptadas por Telegram. En ese caso,
                # reintentamos el mismo aviso como mensaje normal.
                if use_photo and response.status_code == 400:
                    method = "sendMessage"
                    use_photo = False
                    payload.pop("photo", None)
                    payload.pop("caption", None)
                    payload["text"] = text
                    payload["disable_web_page_preview"] = False
                    continue

                if response.status_code == 429:
                    retry_after = 5
                    try:
                        data = response.json()
                        retry_after = int(
                            data.get("parameters", {}).get("retry_after", 5)
                        )
                    except Exception:
                        pass
                    await asyncio.sleep(min(max(retry_after, 2), 30))
                    continue

                if response.status_code >= 500:
                    await asyncio.sleep(min(2 ** attempt, 15))
                    continue

                if response.status_code >= 400:
                    raise RuntimeError(
                        f"Telegram respondió HTTP {response.status_code}."
                    )

                data = response.json()
                if not data.get("ok"):
                    description = str(
                        data.get("description", "respuesta no válida")
                    )
                    raise RuntimeError(
                        f"Telegram rechazó el mensaje: {description[:120]}"
                    )
                return

        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadError,
            httpx.ReadTimeout,
            httpx.WriteError,
            httpx.WriteTimeout,
            httpx.RemoteProtocolError,
        ):
            if attempt < attempts:
                await asyncio.sleep(min(2 ** attempt, 15))
                continue
            raise RuntimeError(
                "La conexión con Telegram se interrumpió varias veces. "
                "Vuelve a intentar más tarde."
            ) from None
        except httpx.HTTPError:
            if attempt < attempts:
                await asyncio.sleep(min(2 ** attempt, 15))
                continue
            raise RuntimeError(
                "No se pudo completar la conexión con Telegram."
            ) from None

    if last_status is not None:
        raise RuntimeError(
            f"Telegram no aceptó el envío después de varios intentos "
            f"(último estado HTTP {last_status})."
        )
    raise RuntimeError("No se pudo enviar el mensaje a Telegram.")


async def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError(
            "Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID."
        )

    settings, sources = load_config(Path("config.json"))
    state = State(Path("state/state.json"))
    state.load()

    print('=== INICIO DE REVISION ===', flush=True)
    print(f'Fuentes configuradas: {len([s for s in sources if s.enabled])}', flush=True)
    deals, errors, pages_scanned = await Scanner(settings).scan(sources)
    alerts = select_alerts(deals, state, settings)
    print(f'Paginas revisadas: {pages_scanned}', flush=True)
    print(f'Ofertas que cumplen filtros: {len(deals)}', flush=True)
    print(f'Alertas nuevas o mejoradas: {len(alerts)}', flush=True)
    if errors:
        print('Incidencias detectadas:', flush=True)
        for err in errors[:10]:
            print(f'- {err}', flush=True)

    now = int(time.time())
    notified: set[str] = set()
    send_errors: list[str] = []

    for alert in alerts:
        deal = alert.deal
        icon = (
            "🚨"
            if deal.discount >= 70
            else "🔥"
            if deal.discount >= 60
            else "🏷️"
        )

        price_lines = [
            f"💰 <b>{html.escape(money(deal.current_price))}</b>"
        ]
        if deal.reference_price is not None:
            price_lines.append(
                f"Referencia: {html.escape(money(deal.reference_price))}"
            )

        seller = deal.seller or "Vendedor no identificado"
        seller_type = (
            "✅ Venta Falabella"
            if "falabella" in seller.lower()
            else "🛍️ Marketplace"
        )

        verification = ""
        calculated = deal.calculated_discount
        if (
            calculated is not None
            and abs(calculated - deal.discount) >= 8
        ):
            verification = (
                f"\nℹ️ Descuento aproximado calculado: {calculated}%."
            )

        extras = ""
        if deal.badges:
            extras = "\n✨ " + " · ".join(
                html.escape(item) for item in deal.badges[:4]
            )

        message = (
            f"{icon} <b>{deal.discount}% DE DESCUENTO</b>\n"
            f"<b>{html.escape(deal.title)}</b>\n\n"
            + "\n".join(price_lines)
            + f"\n🏬 {html.escape(deal.source)}"
            + f"\n👤 {html.escape(seller)} — {seller_type}"
            + f"\n📉 {html.escape(alert.reason)}"
            + verification
            + extras
            + "\n\nVerifica stock, vendedor, cupón y precio final."
        )

        try:
            await send_telegram(token, chat_id, message, deal.url, deal.image_url)
            notified.add(deal.key)
            print(f'ENVIADA: {deal.discount}% | {deal.title[:90]}', flush=True)
        except RuntimeError as exc:
            send_errors.append(str(exc))
            logging.error("No se pudo enviar una alerta: %s", exc)
            print(f'ERROR DE ENVIO: {exc}', flush=True)

        await asyncio.sleep(1.2)

    for deal in deals:
        state.update(deal, now, deal.key in notified)

    state.cleanup(now)
    state.save()

    all_errors = list(errors)
    all_errors.extend(send_errors[:3])

    summary = (
        "🤖 <b>Bot de ofertas ejecutado</b>\n\n"
        f"📄 Páginas revisadas: {pages_scanned}\n"
        f"🏷️ Ofertas con 70% o más y venta Falabella: {len(deals)}\n"
        f"📨 Alertas nuevas enviadas: {len(notified)}"
    )
    if not deals:
        summary += (
            "\n\nℹ️ No se encontraron ofertas que cumplan todos los filtros "
            "en esta revisión."
        )
    elif deals and not alerts:
        summary += (
            "\n\nℹ️ Sí hubo ofertas válidas, pero ya habían sido avisadas "
            "anteriormente y no se repitieron."
        )
    if all_errors:
        summary += "\n\n⚠️ <b>Incidencias</b>\n" + "\n".join(
            f"• {html.escape(error[:220])}" for error in all_errors[:5]
        )
    try:
        await send_telegram(token, chat_id, summary)
        print('Resumen final enviado a Telegram.', flush=True)
    except RuntimeError as exc:
        logging.error(
            "No se pudo enviar el resumen final: %s",
            exc,
        )
        print(f'ERROR EN RESUMEN FINAL: {exc}', flush=True)

    print('=== FIN DE REVISION ===', flush=True)


if __name__ == "__main__":
    asyncio.run(main())
