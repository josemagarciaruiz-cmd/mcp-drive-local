#!/usr/bin/env python3
# Lista TODO lo compartido por enlace PUBLICO que es propiedad del usuario, y
# marca los nombres que parezcan secretos o datos personales. Solo informa.
import os
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
HERE=os.path.dirname(os.path.abspath(__file__))
ev={}
for l in open(os.path.join(HERE,".env")):
    l=l.strip()
    if l and not l.startswith("#") and "=" in l:
        k,v=l.split("=",1); ev[k.strip()]=v.strip()
creds=Credentials(token=None, refresh_token=ev["GOOGLE_REFRESH_TOKEN"],
    token_uri="https://oauth2.googleapis.com/token",
    client_id=ev["GOOGLE_CLIENT_ID"], client_secret=ev["GOOGLE_CLIENT_SECRET"],
    scopes=["https://www.googleapis.com/auth/drive"])
svc=build("drive","v3",credentials=creds,cache_discovery=False)
q="(visibility='anyoneWithLink' or visibility='anyoneCanFind') and trashed=false"
files=[]; tok=None
while True:
    r=svc.files().list(q=q, pageSize=1000, pageToken=tok,
        fields="nextPageToken, files(id,name,mimeType,owners(me))",
        supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    files.extend(r.get("files",[])); tok=r.get("nextPageToken")
    if not tok: break
mios=[f for f in files if (f.get("owners") or [{}])[0].get("me")]
SENS=("token","clave","password","contrase","api","secret","dni","nie","pasaporte","formulario","factura")
print("PUBLICOS_PROPIOS=%d" % len(mios))
for f in sorted(mios,key=lambda x:x["name"].lower()):
    alerta=" <== ALERTA (posible secreto/dato personal)" if any(s in f["name"].lower() for s in SENS) else ""
    print(" -", f["name"], alerta)
if not mios:
    print("Todo en orden: no hay nada compartido por enlace publico.")
