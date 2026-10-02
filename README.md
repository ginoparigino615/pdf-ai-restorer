# PDF AI Restorer Web

## Funzioni
- upload PDF
- elaborazione pagina per pagina
- denoise, contrasto e nitidezza moderata
- OCR italiano + inglese
- PDF con testo selezionabile
- classificazione indicativa delle pagine
- selezione di singole pagine o intervalli
- coda per interventi AI selettivi

## AI
La coda AI è predisposta, ma questa versione NON scarica automaticamente modelli pesanti e non nasconde chiamate esterne. Il prossimo collegamento può usare un model server GPU o un provider esterno.

## Avvio con Docker
docker build -t pdf-ai-restorer .
docker run -p 8000:8000 -v pdfdata:/app/data pdf-ai-restorer

Poi apri http://localhost:8000.

## Per 700–800 pagine
In produzione servono storage persistente e una coda worker. Il progetto salva lo stato delle pagine, quindi l'architettura è già pensata per la ripresa.

Non disabilitare antivirus o protezioni del computer.
