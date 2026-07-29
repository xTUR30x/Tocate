import re
from typing import Dict, Any
from fastapi import FastAPI, HTTPException, Query
import requests
from bs4 import BeautifulSoup

app = FastAPI(
    title="API Scraper de Tasas de Cambio - El Toque",
    description="Endpoint para obtener las tasas del informal en Cuba usando BeautifulSoup4."
)

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

def parse_tasas_html(html_content: str) -> Dict[str, Any]:
    """
    Parsea el contenido HTML con BeautifulSoup para extraer la moneda, 
    su valor en CUP y la variación si existe.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    tasas = {}

    # Buscamos todas las filas <tr> de la tabla
    rows = soup.find_all("tr")

    for row in rows:
        # 1. Extraer la moneda/moneda objetivo (ej. "1 USD", "1 EUR", "1 MLC", etc.)
        currency_cell = row.find("span", id=re.compile(r"^cell-title-v2-"))
        if not currency_cell:
            continue
        
        currency_raw = currency_cell.get_text(strip=True)
        # Limpiamos espacios no divisibles (&nbsp;) y extraemos el código (ej. USD)
        currency = currency_raw.replace("\xa0", " ").strip()

        # 2. Extraer el valor en CUP y posible variación
        value_cell = row.find("td", class_=re.compile(r"pl-3"))
        if not value_cell:
            continue

        # El valor principal está en el span con 'font-extrabold text-lg'
        rate_span = value_cell.find("span", class_=re.compile(r"font-extrabold"))
        if not rate_span:
            continue

        rate_text = rate_span.get_text(strip=True).replace("\xa0", " ")
        
        # Extraer solo el número flotante (ej. "675.00 CUP" -> 675.00)
        match_rate = re.search(r"([\d\.]+)", rate_text)
        rate_val = float(match_rate.group(1)) if match_rate else None

        # 3. Extraer la variación (ej. "+2.98" o "-0.6"), si existe
        change_span = value_cell.find("span", class_=re.compile(r"text-xs"))
        change_val = change_span.get_text(strip=True) if change_span else "0.00"

        tasas[currency] = {
            "tasa_cup": rate_val,
            "variacion": change_val,
            "texto_raw": rate_text
        }

    return tasas

@app.get("/scrape-tasas")
def get_tasas(
    url: str = Query(
        default="https://eltoque.com",
        description="URL de El Toque a consultar"
    )
):
    try:
        # Realizamos la petición HTTP
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(
            status_code=400, 
            detail=f"Error al intentar obtener la página: {str(e)}"
        )

    # Parseamos el HTML obtenido
    data = parse_tasas_html(response.text)

    if not data:
        raise HTTPException(
            status_code=404, 
            detail="No se encontraron datos de tasas en la URL proporcionada."
        )

    return {
        "status": "success",
        "url": url,
        "tasas": data
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)