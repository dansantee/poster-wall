proxy:

cd <unzipped>\poster-wall\proxy
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install flask requests
$env:PLEX_URL="http://192.168.1.5:32400"
$env:PLEX_TOKEN="R9TBSeRe-g6yWqtj5p2s"
$env:SECTION_ID="1"
python app.py


web app:

cd <unzipped>\poster-wall\web
python -m http.server 8088