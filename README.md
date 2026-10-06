# safira-google-feed

Feed de produtos da Safira Armarinhos para o Google Merchant Center, gerado
fora da VNDA, sem alterar nada no site.

**Por que existe:** o feed nativo da VNDA (`/products_google.rss`) manda uma
oferta por produto-pai com as fotos de todas as cores (a Mollet 100g ia com
1.197 fotos; o limite do Google é 10). Por isso os fios mais vendidos ficavam
reprovados com "Too many issues". Além disso, a marca saía sempre como
"Safira Armarinhos" e o GTIN não era enviado.

**O que este feed faz:**
- uma oferta por cor (`g:id` = SKU), agrupadas por `g:item_group_id` (id do pai);
- foto da própria cor + no máximo 10 fotos extras;
- GTIN (EAN do cadastro, com dígito verificador conferido), marca real, cor;
- disponibilidade e preço por cor, lidos da página do produto;
- link da página do produto com `?sku=` (o site ignora o parâmetro).

**Fonte dos dados (somente leitura):** lista de produtos do feed da VNDA +
JSON de variações embutido em cada página de produto (`data-variant` do
`vnda.min.js`). Se a VNDA mudar o tema e esse JSON sumir, o script falha
com "sem dados de variação" e **não grava** o feed (proteção: aborta se mais
de 20% dos produtos derem erro).

**Rodar local:** `python gerar_feed.py feed.xml` (ou `--limite 20` para teste).

**Automação:** GitHub Actions roda todo dia às 05:00 (Brasília) e publica em
GitHub Pages (`.../feed.xml`). Essa URL é a fonte cadastrada no Merchant Center
(subconta 585231049).
