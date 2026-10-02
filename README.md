# PDF AI Restorer — Render Free v2

Versione più veloce e conservativa.

## Cosa fa
- mantiene il colore originale delle scansioni;
- renderizza a 160 DPI di default per ridurre i tempi sul piano Free;
- correzione conservativa di illuminazione/contrasto;
- denoise leggero solo quando utile;
- sharpening leggero;
- deskew;
- OCR Tesseract con coordinate delle parole;
- PDF ricercabile/selezionabile con testo invisibile;
- valutazione indicativa della qualità pagina;
- selezione manuale di pagine/intervalli;
- secondo passaggio di miglioramento sulle pagine selezionate;
- checkpoint periodici;
- storage B2 opzionale per recupero dopo riavvio.

## Nota
La selezione "AI" di questa versione non è ancora un modello generativo/super-resolution GPU: è un secondo passaggio conservativo OpenCV. La vera AI selettiva può essere aggiunta successivamente.

## Deploy
Su Render: Docker + Free. Il servizio usa il filesystem temporaneo di Render; per lavori lunghi configura Backblaze B2.
