from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import time
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger("falabella-v2")


@dataclass(frozen=True)
class Settings:
    min_discount: int
    max_alerts: int
    price_drop_percent: float
    discount_increase: int
    seller_mode: str
    include_keywords: tuple[str, ...]
    exclude_keywords: tuple[str, ...]
    pause_seconds: float
    robots_required: bool
    user_agent: str
    headless: bool


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    min_discount: int
    pages: int
    max_items: int
    scroll_rounds: int


@dataclass(frozen=True)
class Deal:
    source: str
    title: str
    url: str
    discount: int
    current_price: float | None
    reference_price: float | None
    seller: str | None
    badges: tuple[str, ...]

    @property
    def key(self) -> str:
        return hashlib.sha256(normalize_url(self.url).encode("utf-8")).hexdigest()

    @property
    def calculated_discount(self) -> int | None:
        if (
            self.current_price is None
            or self.reference_price is None
            or self.reference_price <= 0
            or self.current_price >= self.reference_price
        ):
            return None
        return round(
            (self.reference_price - self.current_price) * 100 / self.reference_price
        )


@dataclass(frozen=True)
class Alert:
    deal: Deal
    reason: str
    score: float


def clamp_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = minimum
    return max(minimum, min(maximum, value))


def normalize_words(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict.fromkeys(
        str(item).strip().lower()
        for item in value
        if str(item).strip()
    ))


def load_config(path: Path) -> tuple[Settings, list[Source]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("settings", {})
    seller_mode = str(raw.get("seller_mode", "all")).lower()
    if seller_mode not in {"all", "falabella_only"}:
        seller_mode = "all"

    settings = Settings(
        min_discount=clamp_int(raw.get("min_discount", 50), 1, 99),
        max_alerts=clamp_int(raw.get("max_alerts", 20), 1, 40),
        price_drop_percent=max(0.0, float(raw.get("price_drop_percent", 3))),
        discount_increase=clamp_int(raw.get("discount_increase", 3), 1, 30),
        seller_mode=seller_mode,
        include_keywords=normalize_words(raw.get("include_keywords", [])),
        exclude_keywords=normalize_words(raw.get("exclude_keywords", [])),
        pause_seconds=max(2.0, float(raw.get("pause_seconds", 3))),
        robots_required=bool(raw.get("robots_required", True)),
        user_agent=str(raw.get(
            "user_agent",
            "FalabellaDealMonitor/2.0 (monitor personal)"
        )).strip(),
        headless=bool(raw.get("headless", True)),
    )

    sources: list[Source] = []
    for item in data.get("sources", []):
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if not name or not url.startswith(("https://", "http://")):
            continue
        sources.append(Source(
            name=name,
            url=url,
            min_discount=clamp_int(item.get("min_discount", settings.min_discount), 1, 99),
            pages=clamp_int(item.get("pages", 1), 1, 2),
            max_items=clamp_int(item.get("max_items", 35), 1, 80),
            scroll_rounds=clamp_int(item.get("scroll_rounds", 3), 0, 6),
        ))

    if not sources:
        raise ValueError("No hay fuentes activas en config.json.")
    return settings, sources


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    query = [
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() in {"sku", "productid"}
    ]
    return urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path.rstrip("/"),
        "",
        urlencode(query),
        "",
    ))


def page_url(url: str, page_number: int) -> str:
    if page_number <= 1:
        return url
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = str(page_number)
    return urlunparse(parsed._replace(query=urlencode(query)))


def parse_price(raw: str) -> float | None:
    text = raw.replace("S/", "").replace("\u00a0", " ")
    text = re.sub(r"[^\d,.\s]", "", text).replace(" ", "")
    if not text:
        return None

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        tail = text.rsplit(",", 1)[-1]
        text = text.replace(".", "").replace(",", ".") if len(tail) in {1, 2} else text.replace(",", "")
    elif "." in text:
        tail = text.rsplit(".", 1)[-1]
        if len(tail) == 3:
            text = text.replace(".", "")

    try:
        value = float(text)
    except ValueError:
        return None
    return value if 0 < value < 10_000_000 else None


def money(value: float | None) -> str:
    if value is None:
        return "Precio no identificado"
    if math.isclose(value, round(value), abs_tol=0.001):
        return f"S/ {int(round(value)):,}".replace(",", " ")
    return f"S/ {value:,.2f}".replace(",", " ").replace(".", ",")


class State:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {"version": 2, "items": {}}

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("items"), dict):
                self.data = data
        except (OSError, json.JSONDecodeError):
            logger.warning("No se pudo cargar el historial.")

    def previous(self, deal: Deal) -> dict[str, Any] | None:
        value = self.data.get("items", {}).get(deal.key)
        return value if isinstance(value, dict) else None

    def update(self, deal: Deal, now: int, notified: bool) -> None:
        items = self.data.setdefault("items", {})
        old = items.get(deal.key, {})
        items[deal.key] = {
            "title": deal.title,
            "url": normalize_url(deal.url),
            "source": deal.source,
            "seller": deal.seller,
            "current_price": deal.current_price,
            "reference_price": deal.reference_price,
            "discount": deal.discount,
            "first_seen": old.get("first_seen", now) if isinstance(old, dict) else now,
            "last_seen": now,
            "last_notified": now if notified else old.get("last_notified") if isinstance(old, dict) else None,
        }

    def cleanup(self, now: int, days: int = 120) -> None:
        cutoff = now - days * 86400
        items = self.data.setdefault("items", {})
        for key in list(items):
            value = items[key]
            if not isinstance(value, dict) or int(value.get("last_seen", 0)) < cutoff:
                items.pop(key, None)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)


class RobotsPolicy:
    def __init__(self, user_agent: str, required: bool) -> None:
        self.user_agent = user_agent
        self.required = required
        self.cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    async def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

        if robots_url in self.cache:
            parser = self.cache[robots_url]
            return (not self.required) if parser is None else parser.can_fetch(self.user_agent, url)

        try:
            async with httpx.AsyncClient(
                timeout=15,
                follow_redirects=True,
                headers={"User-Agent": self.user_agent},
            ) as client:
                response = await client.get(robots_url)

            if response.status_code == 404:
                parser = urllib.robotparser.RobotFileParser()
                parser.parse([])
                self.cache[robots_url] = parser
                return True

            if response.status_code >= 400:
                self.cache[robots_url] = None
                return not self.required

            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(robots_url)
            parser.parse(response.text.splitlines())
            self.cache[robots_url] = parser
            return parser.can_fetch(self.user_agent, url)
        except httpx.HTTPError:
            self.cache[robots_url] = None
            return not self.required


class Scanner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.robots = RobotsPolicy(settings.user_agent, settings.robots_required)

    async def scan(self, sources: list[Source]) -> tuple[list[Deal], list[str], int]:
        deals: list[Deal] = []
        errors: list[str] = []
        pages_scanned = 0

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=self.settings.headless)
            try:
                for source in sources:
                    for page_number in range(1, source.pages + 1):
                        url = page_url(source.url, page_number)
                        if not await self.robots.allowed(url):
                            errors.append(f"{source.name}: ruta no permitida por robots.txt.")
                            break
                        try:
                            deals.extend(await self._scan_page(browser, source, url))
                            pages_scanned += 1
                        except Exception as exc:
                            errors.append(f"{source.name}: {type(exc).__name__}: {str(exc)[:160]}")
                            if "HTTP 403" in str(exc) or "HTTP 429" in str(exc):
                                break
                        await asyncio.sleep(self.settings.pause_seconds)
            finally:
                await browser.close()

        unique: dict[str, Deal] = {}
        for deal in deals:
            current = unique.get(deal.key)
            if current is None or deal.discount > current.discount:
                unique[deal.key] = deal

        result = [deal for deal in unique.values() if self._passes(deal)]
        result.sort(key=lambda d: (d.discount, -(d.current_price or 10_000_000)), reverse=True)
        return result, errors, pages_scanned

    def _passes(self, deal: Deal) -> bool:
        if deal.discount < self.settings.min_discount:
            return False
        seller = (deal.seller or "").lower()
        if self.settings.seller_mode == "falabella_only" and "falabella" not in seller:
            return False
        text = f"{deal.title} {seller}".lower()
        if self.settings.include_keywords and not any(word in text for word in self.settings.include_keywords):
            return False
        if any(word in text for word in self.settings.exclude_keywords):
            return False
        return True

    async def _scan_page(self, browser: Any, source: Source, url: str) -> list[Deal]:
        page = await browser.new_page(
            viewport={"width": 1365, "height": 900},
            locale="es-PE",
            timezone_id="America/Lima",
        )
        page.set_default_timeout(30000)

        async def route_handler(route: Any) -> None:
            if route.request.resource_type in {"media", "font"}:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", route_handler)

        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            if response and response.status in {403, 429}:
                raise RuntimeError(f"HTTP {response.status}")
            if response and response.status >= 400:
                raise RuntimeError(f"HTTP {response.status}")

            await page.wait_for_timeout(2500)
            body = (await page.locator("body").inner_text()).lower()
            if any(marker in body for marker in ("captcha", "access denied", "acceso denegado", "too many requests")):
                raise RuntimeError("La página mostró bloqueo o CAPTCHA.")

            for _ in range(source.scroll_rounds):
                await page.evaluate("window.scrollBy(0, Math.max(window.innerHeight, 850))")
                await page.wait_for_timeout(850)

            raw_items = await page.evaluate(r"""
            () => {
              const clean = (v) => (v || "")
                .replace(/\u00a0/g, " ")
                .replace(/[ \t]+/g, " ")
                .replace(/\n{2,}/g, "\n")
                .trim();

              const selectors = [
                "[data-pod]",
                "[data-testid*='product-card']",
                "[data-testid*='product']",
                "article",
                "li[class*='product']",
                "div[class*='product-card']",
                "div[class*='productCard']",
                "div[class*='pod']"
              ];

              const nodes = [];
              const seenNodes = new Set();

              for (const selector of selectors) {
                for (const node of document.querySelectorAll(selector)) {
                  if (!seenNodes.has(node)) {
                    seenNodes.add(node);
                    nodes.push(node);
                  }
                }
              }

              if (nodes.length < 8) {
                for (const anchor of document.querySelectorAll("a[href]")) {
                  const href = anchor.getAttribute("href") || "";
                  if (!/\/product\/|\/producto\/|\/p\/|\/ip\//i.test(href)) continue;
                  let parent = anchor;
                  for (let i = 0; i < 5 && parent; i++) {
                    const t = clean(parent.innerText);
                    if (/\d{2}\s*%/.test(t) && /S\/\s*\d/.test(t)) break;
                    parent = parent.parentElement;
                  }
                  if (parent && !seenNodes.has(parent)) {
                    seenNodes.add(parent);
                    nodes.push(parent);
                  }
                }
              }

              const output = [];
              const seenUrls = new Set();

              for (const node of nodes) {
                const text = clean(node.innerText);
                if (!text || text.length < 12 || text.length > 5000) continue;

                const discounts = [...text.matchAll(/(?:^|[\s\-−])(\d{1,2})\s*%/g)]
                  .map(m => Number(m[1]))
                  .filter(v => v >= 1 && v <= 99);

                if (!discounts.length) continue;

                const anchor =
                  [...node.querySelectorAll("a[href]")].find(a =>
                    /\/product\/|\/producto\/|\/p\/|\/ip\//i.test(a.getAttribute("href") || "")
                  ) || node.querySelector("a[href]");

                if (!anchor) continue;

                let url = "";
                try {
                  url = new URL(anchor.getAttribute("href"), location.href).href;
                } catch (_) {
                  continue;
                }
                if (!url || seenUrls.has(url)) continue;

                const titleElement = node.querySelector(
                  "h1,h2,h3,h4,[data-testid*='title'],[class*='title'],[class*='name']"
                );
                const image = node.querySelector("img");
                const title =
                  clean(titleElement?.innerText) ||
                  clean(anchor.getAttribute("aria-label")) ||
                  clean(image?.getAttribute("alt")) ||
                  clean(text.split("\n").find(line =>
                    line.length >= 8 &&
                    !/^(s\/|precio|desde|hasta|cup[oó]n|retiro|llega|cyber|liquidaci[oó]n)/i.test(line)
                  ));

                if (!title) continue;

                const prices = [...text.matchAll(
                  /S\/\s*([\d]{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)/gi
                )].map(m => `S/ ${m[1]}`);

                const sellerMatch = text.match(/(?:^|\n)Por\s+([^\n]{2,100})/i);

                const badges = [];
                for (const badge of ["CMR", "CUPON", "ENVÍO GRATIS", "RETIRO HOY", "LLEGA MAÑANA"]) {
                  if (text.toUpperCase().includes(badge)) badges.push(badge);
                }

                seenUrls.add(url);
                output.push({
                  title: title.slice(0, 240),
                  url,
                  discount: Math.max(...discounts),
                  prices: [...new Set(prices)].slice(0, 5),
                  seller: sellerMatch ? sellerMatch[1].trim() : "",
                  badges
                });
              }

              return output;
            }
            """)

            output: list[Deal] = []
            for item in raw_items[:source.max_items]:
                title = re.sub(r"\s+", " ", str(item.get("title", ""))).strip()[:220]
                item_url = str(item.get("url", "")).strip()
                discount = clamp_int(item.get("discount", 0), 0, 99)

                prices: list[float] = []
                for raw_price in item.get("prices", []):
                    parsed = parse_price(str(raw_price))
                    if parsed is not None and parsed not in prices:
                        prices.append(parsed)

                current_price = prices[0] if prices else None
                reference_price = max(prices) if len(prices) >= 2 else None
                if current_price is not None and reference_price is not None and current_price >= reference_price:
                    reference_price = None

                if title and item_url.startswith(("https://", "http://")) and discount >= source.min_discount:
                    output.append(Deal(
                        source=source.name,
                        title=title,
                        url=item_url,
                        discount=discount,
                        current_price=current_price,
                        reference_price=reference_price,
                        seller=re.sub(r"\s+", " ", str(item.get("seller", ""))).strip()[:120] or None,
                        badges=tuple(str(x).strip() for x in item.get("badges", []) if str(x).strip()),
                    ))
            return output
        except PlaywrightTimeoutError as exc:
            raise RuntimeError("La página demoró demasiado.") from exc
        finally:
            await page.close()


def select_alerts(deals: list[Deal], state: State, settings: Settings) -> list[Alert]:
    alerts: list[Alert] = []

    for deal in deals:
        previous = state.previous(deal)
        if previous is None:
            alerts.append(Alert(deal, "Nueva oferta detectada", 1000 + deal.discount))
            continue

        old_price = previous.get("current_price")
        old_discount = int(previous.get("discount", 0) or 0)

        price_drop = 0.0
        if (
            isinstance(old_price, (int, float))
            and old_price > 0
            and deal.current_price is not None
            and deal.current_price < old_price
        ):
            price_drop = (old_price - deal.current_price) * 100 / old_price

        discount_gain = deal.discount - old_discount

        if price_drop >= settings.price_drop_percent:
            alerts.append(Alert(
                deal,
                f"El precio bajó {price_drop:.1f}% desde la última revisión",
                2000 + price_drop + deal.discount,
            ))
        elif discount_gain >= settings.discount_increase:
            alerts.append(Alert(
                deal,
                f"El descuento subió de {old_discount}% a {deal.discount}%",
                1500 + discount_gain + deal.discount,
            ))

    alerts.sort(key=lambda item: item.score, reverse=True)
    return alerts[:settings.max_alerts]
