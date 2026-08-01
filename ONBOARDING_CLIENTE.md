# Onboarding de cliente — Instrucciones para el Claude del cliente

Objetivo: dejar el conector MCP Drive funcionando sobre el Drive de ESTE cliente,
con las credenciales de Google del propio cliente. Todo se hace en el ordenador del
cliente y con su cuenta de Google. Asi el cliente es "el dueno de su propia app" y
Google no exige verificacion.

Para el asistente (Claude del cliente): ejecuta estos pasos con la extension de
navegador (Claude in Chrome). Donde Google pida iniciar sesion o pulsar "Permitir",
pideselo a la persona: es ella quien consiente con su cuenta.

Resultado: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET y GOOGLE_REFRESH_TOKEN, que se
entregan al instalador.

## Parte A - Proyecto y credenciales en Google Cloud
1. Abre https://console.cloud.google.com/ (persona logueada con SU cuenta).
2. Crear proyecto: selector de proyectos -> "Proyecto nuevo" -> nombre "MCP Drive" -> Crear -> seleccionarlo.
3. Activar API de Drive: https://console.cloud.google.com/apis/library/drive.googleapis.com -> Habilitar.
4. Pantalla de consentimiento: https://console.cloud.google.com/apis/credentials/consent
   - Tipo: Externo -> Crear.
   - Nombre "MCP Drive"; correos = el de la persona. Guardar y continuar.
   - Permisos: anade https://www.googleapis.com/auth/drive -> Guardar y continuar.
   - Usuarios de prueba: vacio. Guardar.
5. PUBLICAR la app ("Publicar app" / "Publish app") y confirmar. (Si se queda en prueba, caduca a los pocos dias.)
6. Credenciales OAuth: https://console.cloud.google.com/apis/credentials
   -> Crear credenciales -> ID de cliente de OAuth -> Tipo "Aplicacion de escritorio" -> Crear.
   - Copia ID de cliente (CLIENT_ID) y Secreto (CLIENT_SECRET).

## Parte B - Token del cliente (REFRESH_TOKEN)
7. Descarga el proyecto (incluye get_refresh_token.py).
8. Ejecuta get_refresh_token.py con el CLIENT_ID y CLIENT_SECRET. Abrira una URL.
9. La persona abre la URL, inicia sesion y pulsa Permitir. Si sale aviso de "app no
   verificada" (es su propia app, es seguro): "Configuracion avanzada" -> "Ir a MCP
   Drive (no seguro)" -> Permitir.
10. Google devuelve un codigo; el ayudante muestra el REFRESH_TOKEN. Copialo.

## Parte C - Instalar con las 3 claves
11. Ejecuta el instalador (Windows: INSTALAR_WINDOWS.bat; Mac: ./instalar.sh) e
    introduce las 3 claves cuando las pida.
12. Abre Claude y comprueba una accion sobre el Drive del cliente.

## Notas
- Las 3 claves son personales del cliente; no se comparten.
- El cliente NO usa ninguna URL del proveedor: su conector corre en su equipo con
  sus claves, sobre su Drive.
- Revocar acceso: borrar las credenciales en la consola, o el permiso en la seccion
  de "Aplicaciones de terceros" de su cuenta de Google.
