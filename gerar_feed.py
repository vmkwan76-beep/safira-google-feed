"""Gera o feed de produtos do Google Merchant Center da Safira Armarinhos.

Uma oferta por variação (cor), agrupadas pelo produto-pai (item_group_id),
com foto da própria cor, GTIN, marca real e no máximo 10 fotos extras.

Fonte dos dados: o próprio site (somente leitura) — a lista de produtos vem
do feed da VNDA (products_google.rss) e os dados de cada cor vêm do JSON de
variações embutido na página do produto. Nada no site é alterado.

Uso: python gerar_feed.py [saida.xml] [--limite N]
"""
import gzip
import html
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from xml.sax.saxutils import escape

SITE = "https://www.safiraarmarinhos.com.br"
FEED_VNDA = SITE + "/products_google.rss"
UA = "Mozilla/5.0 (compatible; SafiraFeedBot/1.0; +https://www.safiraarmarinhos.com.br)"
MAX_FOTOS_EXTRAS = 10
TRABALHADORES = 4

MARCAS = {
    "circulo": "Círculo",
    "pingouin": "Pingouin",
    "euroroma": "EuroRoma",
    "acrilex": "Acrilex",
    "anchor": "Anchor",
    "corrente": "Corrente",
}

# Palavras que ficam em minúsculas no meio do título
MINUSCULAS = {"de", "da", "do", "das", "dos", "e", "com", "para", "em", "a", "o"}
# Siglas/unidades que ficam como estão
MANTER = {"G", "KG", "M", "MM", "CM", "MT", "MTS", "N.", "UN", "ML", "TEX"}
ACENTOS = {"LA": "Lã", "CIRCULO": "Círculo", "LINHA": "Linha", "BEBE": "Bebê",
           "CROCHE": "Crochê", "AGULHA": "Agulha", "LA.": "Lã"}


def baixar(url, tentativas=4):
    for t in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=60) as r:
                dados = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    dados = gzip.decompress(dados)
                return dados.decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            if t == tentativas - 1:
                raise
            time.sleep(5 * (t + 1))


def titulo_bonito(texto):
    """'LA MOLLET 100G CIRCULO' -> 'Lã Mollet 100g Círculo'."""
    palavras = []
    for i, p in enumerate(texto.split()):
        up = p.upper()
        if up in ACENTOS:
            palavras.append(ACENTOS[up])
        elif re.fullmatch(r"\d+([.,]\d+)?(G|KG|M|MM|CM|MT|MTS|ML)", up):
            palavras.append(p.lower())  # 100g, 178m
        elif up in MANTER:
            palavras.append(up)
        elif re.search(r"\d", p):
            palavras.append(up)  # códigos de cor, N.4, 1/2
        elif i > 0 and p.lower() in MINUSCULAS:
            palavras.append(p.lower())
        else:
            palavras.append(p.capitalize())
    return " ".join(palavras)


def gtin_valido(codigo):
    codigo = (codigo or "").strip()
    if not re.fullmatch(r"\d{8}|\d{12,14}", codigo) or set(codigo) == {"0"}:
        return False
    digitos = [int(c) for c in codigo]
    soma = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digitos[:-1])))
    return (10 - soma % 10) % 10 == digitos[-1]


def ler_lista_vnda():
    """Lista de produtos (id, link, descrição) a partir do feed da VNDA."""
    xml = baixar(FEED_VNDA)
    produtos = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
        def tag(nome):
            m = re.search(rf"<{nome}>(.*?)</{nome}>", item, re.S)
            return html.unescape(m.group(1).strip()) if m else ""
        produtos.append({"id": tag("id"), "link": tag("link"), "titulo": tag("title"),
                         "descricao": tag("description")})
    return produtos


def ler_produto(prod):
    pagina = baixar(prod["link"])
    m = re.search(r"id='vndajs' data-variant=\"(.*?)\"", pagina, re.S)
    if not m:
        return prod, None, "sem dados de variação"
    variantes = json.loads(html.unescape(m.group(1)))

    marca = ""
    for bloco in re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', pagina, re.S):
        try:
            d = json.loads(bloco)
        except ValueError:
            continue
        if d.get("@type") == "Product":
            marca = (d.get("brand") or {}).get("name", "") if isinstance(d.get("brand"), dict) else ""
    marca = marca.strip()
    if not marca or marca.lower() == "safira armarinhos":
        # No cadastro a marca do fabricante costuma ser a última palavra do nome
        ultima = re.sub(r"[^\wÀ-ÿ]", "", prod["titulo"].split()[-1]) if prod["titulo"].split() else ""
        if ultima.isalpha() and len(ultima) > 2:
            marca = ultima
    marca = MARCAS.get(marca.lower(), marca.title()) if marca else ""

    # Fotos por SKU (miniaturas da galeria marcadas com data-skus)
    fotos_sku = {}
    fotos_gerais = []
    for bloco in re.findall(r'<div class="image-thumb swiper-slide"(.*?)</figure>', pagina, re.S):
        img = re.search(r'(?:data-src|src)="(https://cdn\.vnda\.com\.br/[^"]+)"', bloco)
        if not img:
            continue
        url = re.sub(r"cdn\.vnda\.com\.br/\d+x/", "cdn.vnda.com.br/", img.group(1))
        skus = re.search(r'data-skus="([^"]*)"', bloco)
        if skus:
            for s in filter(None, skus.group(1).split(",")):
                fotos_sku.setdefault(s.strip(), []).append(url)
        else:
            fotos_gerais.append(url)

    titulo_m = re.search(r"<title>(.*?)</title>", pagina, re.S)
    prod["titulo_site"] = html.unescape(titulo_m.group(1).strip()) if titulo_m else ""
    prod["marca"] = marca
    prod["fotos_sku"] = fotos_sku
    prod["fotos_gerais"] = list(dict.fromkeys(fotos_gerais))
    return prod, variantes, None


def nome_cor(nome_variante, titulo_pai):
    """'LA MOLLET 100G CIRCULO - 010 - BRANCO' -> '010 - BRANCO'."""
    resto = nome_variante
    if resto.upper().startswith(titulo_pai.upper()):
        resto = resto[len(titulo_pai):]
    return re.sub(r"\s+-\s+", " ", resto.strip(" -"))


def montar_itens(prod, variantes):
    itens = []
    unica = len(variantes) == 1
    base = titulo_bonito(prod["titulo"])
    descricao = re.sub(r"\n{3,}", "\n\n", prod["descricao"]).strip()[:5000]
    for v in variantes:
        cor = "" if unica else nome_cor(v.get("name", ""), prod["titulo"])
        titulo = base
        if cor:
            titulo = f"{base} - Cor {titulo_bonito(cor)}"
        foto = v.get("image_url") or ""
        if foto.startswith("//"):
            foto = "https:" + foto
        extras = [f for f in prod["fotos_sku"].get(v["sku"], []) if f.split("?")[0] != foto.split("?")[0]]
        extras += [f for f in prod["fotos_gerais"] if f not in extras]
        extras = extras[:MAX_FOTOS_EXTRAS]
        if not foto and extras:
            foto = extras.pop(0)
        if not foto:
            continue
        disponivel = v.get("available") and (v.get("available_quantity") or 0) > 0
        preco = float(v.get("price") or 0)
        promo = float(v.get("sale_price") or 0)
        if preco <= 0:
            continue
        item = {
            "id": v["sku"],
            "item_group_id": None if unica else prod["id"],
            "title": titulo[:150],
            "description": descricao or titulo,
            "link": prod["link"] if unica else f'{prod["link"]}?sku={v["sku"]}',
            "image_link": foto,
            "additional_image_link": extras,
            "availability": "in_stock" if disponivel else "out_of_stock",
            "price": f"{preco:.2f} BRL",
            "sale_price": f"{promo:.2f} BRL" if 0 < promo < preco else None,
            "condition": "new",
            "brand": prod["marca"] or None,
            "gtin": v.get("barcode") if gtin_valido(v.get("barcode")) else None,
            "mpn": None,
            "color": titulo_bonito(cor) if cor else None,
            "shipping_weight": f'{v["weight"]:.3f} kg' if v.get("weight") else None,
        }
        if not item["gtin"]:
            if item["brand"]:
                item["mpn"] = v["sku"]
            else:
                item["identifier_exists"] = "no"
        itens.append(item)
    return itens


def gerar_xml(itens):
    linhas = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<rss version="2.0" xmlns:g="http://base.google.com/ns/1.0">', "<channel>",
              "<title>Safira Armarinhos</title>", f"<link>{SITE}</link>",
              "<description>Feed de produtos Safira Armarinhos (por cor)</description>"]
    for it in itens:
        linhas.append("<item>")
        for chave, valor in it.items():
            if valor in (None, "", []):
                continue
            tag = chave if chave in ("title", "description", "link") else "g:" + chave
            for v in (valor if isinstance(valor, list) else [valor]):
                linhas.append(f"<{tag}>{escape(str(v))}</{tag}>")
        linhas.append("</item>")
    linhas += ["</channel>", "</rss>"]
    return "\n".join(linhas)


def main():
    args = sys.argv[1:]
    saida = next((a for a in args if not a.startswith("--") and not a.isdigit()), "feed.xml")
    limite = int(args[args.index("--limite") + 1]) if "--limite" in args else None

    produtos = ler_lista_vnda()
    if limite:
        produtos = produtos[:limite]
    print(f"{len(produtos)} produtos no feed da VNDA", flush=True)

    itens, erros = [], []
    with ThreadPoolExecutor(TRABALHADORES) as ex:
        for n, fut in enumerate(ex.map(lambda p: _seguro(p), produtos), 1):
            prod, variantes, erro = fut
            if erro:
                erros.append((prod["id"], prod["link"], erro))
            else:
                itens.extend(montar_itens(prod, variantes))
            if n % 100 == 0:
                print(f"  {n}/{len(produtos)} produtos lidos, {len(itens)} ofertas", flush=True)

    if len(produtos) > 50 and len(erros) > len(produtos) * 0.2:
        sys.exit(f"Muitos erros ({len(erros)}); feed NÃO gravado para não derrubar o catálogo.")

    with open(saida, "w", encoding="utf-8") as f:
        f.write(gerar_xml(itens))
    em_estoque = sum(1 for i in itens if i["availability"] == "in_stock")
    print(f"OK: {len(itens)} ofertas ({em_estoque} em estoque), {len(erros)} produtos com erro -> {saida}")
    for e in erros[:20]:
        print("  erro:", *e)


def _seguro(prod):
    try:
        return ler_produto(prod)
    except Exception as e:  # noqa: BLE001
        return prod, None, f"{type(e).__name__}: {e}"


if __name__ == "__main__":
    main()
