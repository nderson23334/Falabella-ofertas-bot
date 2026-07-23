# Falabella Bot V2 mejorado

Esta versión está diseñada para encontrar más ofertas de Falabella y funcionar
gratuitamente mediante GitHub Actions con la laptop apagada.

## Incluye

- Ofertas generales y varias categorías.
- Segunda página de ofertas generales.
- Filtro desde 50%.
- Historial para evitar duplicados.
- Nueva alerta si baja el precio o aumenta el descuento.
- Precio, vendedor, fuente, porcentaje y enlace.
- Distinción entre venta Falabella y Marketplace.
- Exclusión inicial de reacondicionados y Open Box.
- Ejecución automática cada 20 minutos desde GitHub.

No incluye proxies, CAPTCHA ni técnicas para evadir bloqueos.

## Prueba en Windows

1. Ejecuta `1_CONFIGURAR_WINDOWS.bat`.
2. Pega el token y el chat ID obtenido con `/miid`.
3. Ejecuta `2_PROBAR_WINDOWS.bat`.

## Cambiar el filtro

Abre `config.json` con el Bloc de notas.

Descuento mínimo:

```json
"min_discount": 50
```

Solo venta directa de Falabella:

```json
"seller_mode": "falabella_only"
```

Buscar marcas concretas:

```json
"include_keywords": ["nike", "adidas", "apple"]
```

Con la lista vacía se aceptan todas.
