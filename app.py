import streamlit as st
import pandas as pd
import os # Wird für statische Pfade benötigt
import datetime
import uuid # Wird für eindeutige Schlüssel benötigt
import re # Modul für reguläre Ausdrücke (Datumserkennung)
import requests # Für API-Anfragen an Open Food Facts
import cv2 # OpenCV für die Bildverarbeitung
import numpy as np # Wird von OpenCV benötigt
import io # Um Bilddaten im Speicher zu verarbeiten
from pyzbar.pyzbar import decode as pyzbar_decode # Importiere die pyzbar-Bibliothek

# --- Seitenkonfiguration ---
# Dies sollte der erste Streamlit-Befehl im Skript sein.
st.set_page_config(
    page_title="Inventar-App",
    page_icon="static/icons/icon-192x192.png", # Verwendet jetzt dein eigenes Icon
    layout="wide"
)

# --- PWA Konfiguration ---
# Fügt HTML in den <head> der Seite ein, um die PWA-Funktionalität zu ermöglichen.
# Dies beinhaltet das Verlinken des Manifests und das Registrieren des Service Workers.
# Wir verwenden absolute Pfade (/static/...), um sicherzustellen, dass sie immer gefunden werden.
st.markdown("""
<meta name="theme-color" content="#000000">
<link rel="manifest" href="/static/manifest.json">
<script>
if ('serviceWorker' in navigator) {
    // Wir stellen sicher, dass der Service Worker vom Root-Pfad aus registriert wird.
    // Das ist wichtig, damit er alle Anfragen der App kontrollieren kann.
    window.addEventListener('load', function() {
        navigator.serviceWorker.register('/static/sw.js', { scope: '/' }).then(function(registration) {
            console.log('Service Worker registration successful with scope: ', registration.scope);
        }).catch(function(error) {
            console.log('Service Worker registration failed: ', error);
        });
    });
}</script>""", unsafe_allow_html=True)

# --- Datenladefunktion ---
# Der Decorator @st.cache_data sorgt für intelligentes Caching. Die Daten werden
@st.cache_data
def lade_daten(dateipfad):
    """Lädt die Inventardaten aus einer CSV-Datei und gibt einen DataFrame zurück."""
    try:
        df = pd.read_csv(dateipfad, encoding='utf-8')
        # Füge eine eindeutige ID zu jeder Zeile hinzu, falls sie nicht existiert.
        # Dies ist entscheidend für die Bearbeitungslogik.
        if 'id' not in df.columns:
            df['id'] = [str(uuid.uuid4()) for _ in range(len(df))]
        # Füge die Barcode-Spalte hinzu, falls sie fehlt (für Abwärtskompatibilität)
        if 'barcode' not in df.columns:
            df['barcode'] = ""
        
        # Stelle sicher, dass Spaltentypen korrekt sind
        df['ablaufdatum'] = pd.to_datetime(df['ablaufdatum']).dt.date
        df['barcode'] = df['barcode'].astype(str)
        df['menge'] = pd.to_numeric(df['menge'], errors='coerce').fillna(0)
        
        return df
    except FileNotFoundError:
        # Wenn die CSV-Datei noch nicht existiert, wird ein leerer DataFrame
        # mit den erwarteten Spalten zurückgegeben, um Fehler zu vermeiden.
        return pd.DataFrame(columns=['name', 'menge', 'einheit', 'ablaufdatum', 'kategorie', 'id', 'barcode'])

# --- Daten Speicherfunktion ---
def speichere_daten(dateipfad, dataframe):
    """Speichert den DataFrame als CSV-Datei."""
    dataframe.to_csv(dateipfad, index=False)

# --- Hauptteil der App ---
st.title("🧊 Inventar-App für den Abstellraum")
st.write("Dein digitaler Helfer für einen perfekten Überblick über deine Vorräte.")

# Dateipfad zur CSV-Datei definieren
CSV_DATEIPFAD = 'inventar.csv'

# Daten mithilfe unserer Funktion laden
inventar_df = lade_daten(CSV_DATEIPFAD)

# --- Globale Optionen ---
# Definiere Konstanten und Optionen hier, damit sie im gesamten Skript verfügbar sind.
EINHEITEN = ["Packung", "Dose", "Flasche", "kg", "g", "L", "ml"]
STANDARD_KATEGORIEN = ["Grundnahrungsmittel", "Backzutaten", "Kühlware", "Konserven", "Gewürze", "Sonstiges"]
# Erstelle die Liste der Kategorie-Optionen dynamisch aus den Standardkategorien und denen, die bereits im Inventar vorhanden sind.
kategorie_optionen = sorted(list(set(STANDARD_KATEGORIEN + inventar_df['kategorie'].dropna().unique().tolist())))

# Initialisiere den Session State für die Bearbeitung
if 'editing_id' not in st.session_state:
    st.session_state.editing_id = None
# Initialisiere den Session State für den gescannten Produktnamen
if 'scanned_product_name' not in st.session_state:
    st.session_state.scanned_product_name = ""
# Initialisiere den Session State für den Bestätigungs-Dialog
if 'pending_barcode' not in st.session_state:
    st.session_state.pending_barcode = None
# Initialisiere den Session State für das Suchergebnis im Dialog
if 'barcode_search_result' not in st.session_state:
    st.session_state.barcode_search_result = None
if 'item_to_update_id' not in st.session_state:
    st.session_state.item_to_update_id = None


def get_product_name_from_off(product):
    """
    Extrahiert den bestmöglichen Produktnamen aus den Open Food Facts Daten,
    indem verschiedene Namensfelder in einer priorisierten Reihenfolge durchsucht
    und der Markenname vorangestellt wird.
    """
    if not product:
        return ""

    # Eine Liste von möglichen Namensfeldern, nach Priorität geordnet.
    name_fields = [
        "product_name_de",
        "product_name",
        "generic_name_de",
        "generic_name",
        "product_name_en",
        "generic_name_en"
    ]

    product_name = ""
    for field in name_fields:
        name = product.get(field, "").strip()
        if name:
            product_name = name
            break # Wir haben den ersten gültigen Namen gefunden, also beenden wir die Schleife.

    brands = product.get("brands", "").strip()
    # Füge die Marke hinzu, wenn sie existiert und nicht bereits im Namen enthalten ist.
    if brands and brands.lower() not in product_name.lower():
        return f"{brands} {product_name}"
    
    return product_name

# --- Helferfunktion zur Kategorie-Erkennung ---
def schlage_kategorie_vor(produktname, kategorien_liste):
    """
    Schlägt eine Kategorie basierend auf Schlüsselwörtern im Produktnamen vor.
    Gibt den Namen der vorgeschlagenen Kategorie zurück.
    """
    produktname_lower = produktname.lower()

    # Schlüsselwörter für jede Kategorie (kann erweitert werden)
    kategorie_keywords = {
        "Konserven": ["erbsen", "bohnen", "mais", "linsen", "kichererbsen", "tomaten", "thunfisch", "dose"],
        "Grundnahrungsmittel": ["nudeln", "reis", "pasta", "mehl", "zucker", "öl", "essig", "haferflocken", "müsli", "brot"],
        "Backzutaten": ["backpulver", "hefe", "vanillezucker", "kuvertüre", "mandeln", "nüsse", "backmischung"],
        "Kühlware": ["joghurt", "milch", "käse", "butter", "quark", "wurst", "tofu", "frisch"],
        "Gewürze": ["salz", "pfeffer", "paprika", "curry", "oregano", "basilikum", "kräuter"]
    }

    for kategorie, keywords in kategorie_keywords.items():
        if any(keyword in produktname_lower for keyword in keywords):
            if kategorie in kategorien_liste:
                return kategorie

    # Fallback-Kategorie, wenn nichts gefunden wurde
    return "Sonstiges" if "Sonstiges" in kategorien_liste else kategorien_liste[0]

# --- Helferfunktion zur Einheiten-Erkennung ---
def schlage_einheit_vor(produktname, einheiten_liste, vorgeschlagene_kategorie=None):
    """
    Schlägt eine Einheit basierend auf Schlüsselwörtern im Produktnamen vor.
    Berücksichtigt auch die vorgeschlagene Kategorie für eine bessere Genauigkeit.
    Gibt den Namen der vorgeschlagenen Einheit zurück.
    """
    produktname_lower = produktname.lower()

    # Logik basierend auf der Kategorie: Wenn es eine Konserve ist, ist es wahrscheinlich eine Dose.
    # Dies hat Vorrang vor der Keyword-Suche.
    if vorgeschlagene_kategorie == "Konserven" and "Dose" in einheiten_liste:
        return "Dose"

    # Schlüsselwörter für Einheiten. Die Schlüssel müssen exakt den Werten in der EINHEITEN-Liste entsprechen.
    einheit_keywords = {
        "Flasche": ["flasche", "flaschen", "soße", "sauce", "ketchup", "sirup", "öl", "essig"],
        "Dose": ["dose", "dosen", "konserve"],
        "Packung": ["packung", "pack", "beutel", "tüte"],
        "L": ["saft", "milch", " liter"], # Leerzeichen vor "liter" um "milliliter" zu vermeiden
        "ml": ["ml", "milliliter"],
        "kg": ["kg", "kilo", "kilogramm"],
        "g": [" g ", "gramm"] # Leerzeichen um "g" von anderen Buchstaben zu isolieren
    }

    for einheit, keywords in einheit_keywords.items():
        if any(keyword in produktname_lower for keyword in keywords) and einheit in einheiten_liste:
            return einheit

    # Fallback-Einheit, wenn nichts gefunden wurde
    return "Packung" if "Packung" in einheiten_liste else einheiten_liste[0]

# --- Helferfunktion zur Datumserkennung ---
def schlage_ablaufdatum_vor(produktname):
    """
    Sucht im Produktnamen nach einem Datum und gibt es als datetime.date-Objekt zurück.
    Gibt None zurück, wenn kein Datum gefunden wird.
    """
    produktname_lower = produktname.lower()
    heute = datetime.date.today()

    # Muster 1: DD.MM.YYYY oder DD.MM.YY
    match = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{2,4})', produktname_lower)
    if match:
        try:
            tag, monat = int(match.group(1)), int(match.group(2))
            jahr = int(match.group(3))
            if jahr < 100: # Zweistelliges Jahr (z.B. 26)
                jahr += 2000
            vorgeschlagenes_datum = datetime.date(jahr, monat, tag)
            if vorgeschlagenes_datum >= heute:
                return vorgeschlagenes_datum
        except (ValueError, TypeError):
            pass # Ungültiges Datum (z.B. 32.13.2025)

    # Muster 2: MM/YYYY oder MM/YY (typisch für MHD)
    match = re.search(r'(\d{1,2})/(\d{2,4})', produktname_lower)
    if match:
        try:
            monat = int(match.group(1))
            jahr = int(match.group(2))
            if jahr < 100:
                jahr += 2000
            # Wenn kein Tag angegeben ist, nehmen wir den letzten Tag des Monats
            import calendar
            letzter_tag = calendar.monthrange(jahr, monat)[1]
            vorgeschlagenes_datum = datetime.date(jahr, monat, letzter_tag)
            if vorgeschlagenes_datum >= heute:
                return vorgeschlagenes_datum
        except (ValueError, TypeError):
            pass

    # Kein Datum gefunden
    return None


# --- Debug-Info: Zeige installierte Versionen an ---
st.sidebar.info(f"Streamlit Version: {st.__version__}")
st.sidebar.info(f"Pandas Version: {pd.__version__}")

# --- Dialog zum Hinzufügen/Aktualisieren per Barcode ---
@st.dialog("Artikel per Barcode verwalten")
def barcode_item_dialog():
    global inventar_df # Informiere die Funktion, dass wir die globale Variable ändern wollen.

    barcode = st.session_state.pending_barcode
    item_id = st.session_state.item_to_update_id

    # --- FALL 1: Barcode gehört zu einem existierenden Artikel ---
    if item_id:
        artikel = inventar_df[inventar_df['id'] == item_id].iloc[0]
        st.success(f"Artikel '{artikel['name']}' bereits im Inventar vorhanden.")
        st.write(f"Aktuelle Menge: **{artikel['menge']} {artikel['einheit']}**")
        
        menge_zum_hinzufuegen = st.number_input("Menge zum Hinzufügen", min_value=1, step=1, value=1)

        col1, col2 = st.columns(2)
        if col1.button("Menge erhöhen", use_container_width=True, type="primary"):
            idx_to_update = inventar_df[inventar_df['id'] == item_id].index[0]
            inventar_df.loc[idx_to_update, 'menge'] += menge_zum_hinzufuegen
            speichere_daten(CSV_DATEIPFAD, inventar_df)
            st.toast(f"Menge von '{artikel['name']}' auf {inventar_df.loc[idx_to_update, 'menge']} erhöht!")
            st.session_state.pending_barcode = None
            st.session_state.item_to_update_id = None
            st.cache_data.clear()
            st.rerun()

        if col2.button("Schließen", use_container_width=True):
            st.session_state.pending_barcode = None
            st.session_state.item_to_update_id = None
            st.rerun()
        return

    # --- FALL 2: Neuer Barcode, Produkt wird in API gesucht ---
    if st.session_state.barcode_search_result is None:
        with st.spinner(f"Suche nach Produkt für neuen Barcode '{barcode}'..."):
            try:
                product_name_found = ""
                # 1. Versuch: Open Food Facts
                # Wir verwenden die v0 API, da sie oft robustere Ergebnisse liefert.
                # Ein User-Agent wird für die Anfrage empfohlen.
                api_url = f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
                headers = {
                    'User-Agent': 'InventarApp/1.0 (PowerShell; Windows) - https://github.com/your-repo'
                }
                response = requests.get(api_url, headers=headers, timeout=10)
                if response.status_code == 200 and response.json().get("product"):
                    product_name_found = get_product_name_from_off(response.json().get("product"))
                # 2. Versuch: UPCitemdb
                if not product_name_found:
                    fallback_url = f"https://api.upcitemdb.com/prod/trial/lookup?upc={barcode}"
                    fallback_response = requests.get(fallback_url, timeout=5)
                    if fallback_response.status_code == 200 and fallback_response.json().get('items'):
                        product_name_found = fallback_response.json()['items'][0].get('title', "")
            except requests.RequestException as e:
                st.error(f"Fehler bei der API-Anfrage: {e}")
                product_name_found = "API_ERROR"
        st.session_state.barcode_search_result = product_name_found or "NOT_FOUND"
        st.rerun()

    # --- FALL 3: Suchergebnis für neuen Barcode wird angezeigt ---
    result = st.session_state.barcode_search_result
    if result == "NOT_FOUND" or result == "API_ERROR":
        st.warning(f"Produkt für Barcode '{barcode}' konnte in keiner Datenbank gefunden werden.")
        st.markdown(f"Du kannst es manuell hinzufügen oder [auf Google suchen](https://www.google.com/search?q={barcode}).")
        if st.button("Schließen"):
            st.session_state.pending_barcode = None
            st.session_state.barcode_search_result = None
            st.rerun()
    else:
        st.success(f"Produkt gefunden: **{result}**")
        st.write("Bitte vervollständige die Angaben, um den Artikel hinzuzufügen.")
        with st.form("new_item_from_scan_form"):
            name = st.text_input("Name des Artikels", value=result)
            col1, col2 = st.columns(2)
            menge = col1.number_input("Menge", min_value=1, step=1, value=1)

            # Schlage eine Kategorie vor und finde den Index für die Vorauswahl
            vorgeschlagene_kategorie = schlage_kategorie_vor(result, kategorie_optionen)
            try:
                kategorie_index = kategorie_optionen.index(vorgeschlagene_kategorie)
            except ValueError:
                kategorie_index = 0 # Fallback, falls die Kategorie nicht in der Liste ist
            kategorie = st.selectbox("Kategorie", options=kategorie_optionen, index=kategorie_index)
            
            # Schlage eine Einheit vor (unter Berücksichtigung der Kategorie) und finde den Index
            vorgeschlagene_einheit = schlage_einheit_vor(result, EINHEITEN, vorgeschlagene_kategorie=vorgeschlagene_kategorie)
            try:
                einheit_index = EINHEITEN.index(vorgeschlagene_einheit)
            except ValueError:
                einheit_index = 0 # Fallback
            einheit = col2.selectbox("Einheit", options=EINHEITEN, index=einheit_index)

            # Schlage ein Ablaufdatum vor, falls es im Namen gefunden wird
            vorgeschlagenes_ablaufdatum = schlage_ablaufdatum_vor(result)
            # Setze den Standardwert: entweder das gefundene Datum oder heute + 1 Jahr
            default_ablaufdatum = vorgeschlagenes_ablaufdatum if vorgeschlagenes_ablaufdatum else datetime.date.today()
            ablaufdatum = st.date_input(
                "Ablaufdatum (Bitte prüfen!)", value=default_ablaufdatum, min_value=datetime.date.today(), help="Dieses Feld ist rot, um dich daran zu erinnern, das Datum zu überprüfen."
            )
            
            submitted = st.form_submit_button("Artikel speichern", type="primary")
            if submitted:
                if not name:
                    st.error("Bitte gib einen Namen an.")
                else:
                    # --- NEUE LOGIK: Prüfe auf existierenden Artikel ohne Barcode ---
                    mask = (inventar_df['name'].str.lower() == name.lower()) & \
                           ((inventar_df['barcode'] == '') | (inventar_df['barcode'].isna()) | (inventar_df['barcode'] == 'nan'))
                    possible_matches = inventar_df[mask]

                    if not possible_matches.empty:
                        # Nimm das erste Match und aktualisiere es
                        item_id_to_update = possible_matches.iloc[0]['id']
                        idx_to_update = inventar_df[inventar_df['id'] == item_id_to_update].index[0]

                        # Füge den Barcode hinzu und erhöhe die Menge
                        inventar_df.loc[idx_to_update, 'barcode'] = barcode
                        inventar_df.loc[idx_to_update, 'menge'] += menge
                        
                        speichere_daten(CSV_DATEIPFAD, inventar_df)
                        st.toast(f"Barcode zu '{name}' hinzugefügt & Menge erhöht!")
                    else:
                        # --- ALTE LOGIK: Füge neuen Artikel hinzu ---
                        neuer_artikel_df = pd.DataFrame([{
                            "id": str(uuid.uuid4()), "name": name, "menge": menge,
                            "einheit": einheit, "ablaufdatum": ablaufdatum,
                            "kategorie": kategorie, "barcode": barcode
                        }])
                        inventar_df = pd.concat([inventar_df, neuer_artikel_df], ignore_index=True)
                        speichere_daten(CSV_DATEIPFAD, inventar_df)
                        st.toast(f"Artikel '{name}' wurde neu hinzugefügt!")
                    
                    # Gemeinsame Aktionen nach dem Speichern
                    st.session_state.pending_barcode = None
                    st.session_state.barcode_search_result = None
                    st.cache_data.clear()
                    st.rerun()

        if st.button("Abbrechen"):
            st.session_state.pending_barcode = None
            st.session_state.barcode_search_result = None
            st.rerun()


# --- Dialog zur Bestätigung des Barcodes ---
@st.dialog("Barcode erkannt!")
def barcode_confirm_dialog():
    barcode_data = st.session_state.pending_barcode

    # Phase 1: Suche wurde noch nicht gestartet
    if st.session_state.barcode_search_result is None:
        st.markdown(f"Der folgende Barcode wurde erkannt:")
        st.code(barcode_data, language=None)
        st.markdown("Möchtest du nach diesem Produkt suchen?")
        
        col1, col2 = st.columns(2)
        if col1.button("Ja, suchen", use_container_width=True, type="primary"):
            with st.spinner(f"Suche nach Produkt für Barcode '{barcode_data}'..."):
                try:
                    product_name_found = ""

                    # 1. Versuch: Open Food Facts
                    # Wir verwenden die v0 API, da sie oft robustere Ergebnisse liefert.
                    # Ein User-Agent wird für die Anfrage empfohlen.
                    api_url = f"https://world.openfoodfacts.org/api/v0/product/{barcode_data}.json"
                    headers = {
                        'User-Agent': 'InventarApp/1.0 (PowerShell; Windows) - https://github.com/your-repo'
                    }
                    response = requests.get(api_url, headers=headers, timeout=10)
                    json_response = response.json()
                    
                    if response.status_code == 200 and json_response.get("product"):
                        # Nutze die neue Helferfunktion, um den besten Namen zu finden
                        product_name_found = get_product_name_from_off(json_response.get("product"))

                    # 2. Versuch: UPCitemdb (nur wenn der erste Versuch immer noch nichts ergab)
                    if not product_name_found:
                        fallback_url = f"https://api.upcitemdb.com/prod/trial/lookup?upc={barcode_data}"
                        fallback_response = requests.get(fallback_url, timeout=5)
                        if fallback_response.status_code == 200:
                            items = fallback_response.json().get('items', [])
                            if items:
                                product_name_found = items[0].get('title', "")
                except requests.RequestException as e:
                    st.error(f"Fehler bei der API-Anfrage: {e}")
                    product_name_found = "API_ERROR"

            st.session_state.barcode_search_result = product_name_found or "NOT_FOUND"
            st.rerun() # Lade den Dialog neu, um das Ergebnis anzuzeigen

        if col2.button("Nein, abbrechen", use_container_width=True):
            st.session_state.pending_barcode = None
            st.rerun()

    # Phase 2: Suche ist abgeschlossen, zeige das Ergebnis an
    else:
        result = st.session_state.barcode_search_result
        if result != "NOT_FOUND" and result != "API_ERROR":
            st.success(f"Produkt gefunden: **{result}**")
            st.session_state.scanned_product_name = result
        else:
            st.warning(f"Produkt für Barcode '{barcode_data}' konnte in keiner der angebundenen Datenbanken gefunden werden.")
            google_search_url = f"https://www.google.com/search?q={barcode_data}"
            st.markdown("Du kannst versuchen, das Produkt manuell über Google zu finden:")
            st.markdown(f"**[Auf Google nach '{barcode_data}' suchen]({google_search_url})**")
        
        if st.button("Schließen", use_container_width=True):
            st.session_state.barcode_search_result = None
            st.session_state.pending_barcode = None
            st.rerun()

# --- Barcode-Scanner mit Foto-Upload ---
st.header("Artikel per Barcode scannen")

scan_method = st.radio(
    "Wähle die Scan-Methode",
    ["Live-Kamera (empfohlen für PWA)", "Foto hochladen (zuverlässige Alternative)"],
    horizontal=True,
    label_visibility="collapsed"
)

if scan_method == "Live-Kamera (empfohlen für PWA)":
    img_file_buffer = st.camera_input("Richte die Kamera auf einen Barcode", key="barcode_camera")
else:
    img_file_buffer = st.file_uploader("Lade ein Foto von einem Barcode hoch", type=['png', 'jpg', 'jpeg'], key="barcode_uploader")

if img_file_buffer:
    # Lese die Bilddaten (egal ob von Kamera oder Upload) und konvertiere sie für die Analyse
    bytes_data = img_file_buffer.getvalue()
    cv2_img = cv2.imdecode(np.frombuffer(bytes_data, np.uint8), cv2.IMREAD_COLOR)
    
    # Führe die Barcode-Erkennung mit pyzbar durch
    detected_barcodes = pyzbar_decode(cv2_img)
    
    if not detected_barcodes:
        st.error("Kein Barcode im Bild erkannt. Bitte versuche es erneut mit besserer Beleuchtung und Schärfe.")
    else:
        barcode_data = detected_barcodes[0].data.decode("utf-8")
        
        # Setze alle relevanten Session States zurück
        st.session_state.barcode_search_result = None
        st.session_state.item_to_update_id = None
        st.session_state.pending_barcode = barcode_data

        # Prüfe, ob der Barcode bereits im Inventar existiert
        existing_item = inventar_df[inventar_df['barcode'] == barcode_data]
        if not existing_item.empty:
            st.session_state.item_to_update_id = existing_item.iloc[0]['id']

        st.rerun()

if st.session_state.pending_barcode:
    barcode_item_dialog()

# --- Neuen Artikel hinzufügen (Formular) ---
st.header("Neuen Artikel hinzufügen")

# Die Auswahl der Einheit erfolgt außerhalb des Formulars,
# damit sich das Mengen-Feld dynamisch an die Einheit anpassen kann.
ganzzahl_einheiten = ["Dose", "Packung", "Flasche"]
einheit = st.selectbox("Einheit", options=EINHEITEN)
    
with st.form(key="artikel_formular", clear_on_submit=True):
    # Eingabefelder für den neuen Artikel in zwei Spalten anordnen
    col1, col2 = st.columns(2)

    with col1:
        name = st.text_input(
            "Name des Artikels", 
            value=st.session_state.scanned_product_name, # Hier wird der gescannte Name eingefügt
            placeholder="z.B. Linsen, Tofu oder Backkakao"
        )
        # Das Mengen-Feld ist wieder im Formular. Es passt sich an die "Einheit"-Auswahl darüber an.
        if einheit in ganzzahl_einheiten:
            menge = st.number_input("Menge", min_value=0, step=1, format="%d")
        else:
            menge = st.number_input("Menge", min_value=0.0, step=0.1, format="%.2f")
        
    with col2:
        ablaufdatum = st.date_input(
            "Ablaufdatum (Bitte prüfen!)", value=datetime.date.today(), min_value=datetime.date.today(), help="Dieses Feld ist rot, um dich daran zu erinnern, das Datum zu überprüfen."
        )
        kategorie = st.selectbox("Kategorie", options=kategorie_optionen)
    # Der Button zum Absenden des Formulars
    submitted = st.form_submit_button("Artikel hinzufügen")

if submitted:
    if not name:
        st.error("Bitte gib einen Namen für den Artikel ein!")
    else:
        # Erstelle einen neuen DataFrame für den neuen Artikel
        neuer_artikel_df = pd.DataFrame([{
            "name": name,
            "menge": menge,
            "einheit": einheit,
            "ablaufdatum": ablaufdatum,
            "kategorie": kategorie,
            "id": str(uuid.uuid4()), # Füge eine neue ID hinzu
            "barcode": "" # Leerer Barcode, da manuell hinzugefügt
        }])
        # Füge den neuen Artikel zum bestehenden Inventar hinzu
        inventar_df = pd.concat([inventar_df, neuer_artikel_df], ignore_index=True)
        # Speichere den aktualisierten DataFrame
        speichere_daten(CSV_DATEIPFAD, inventar_df)
        st.success(f"Artikel '{name}' wurde zum Inventar hinzugefügt!")
        # Setze den gescannten Namen zurück, damit das Feld beim nächsten Mal leer ist
        st.session_state.scanned_product_name = ""
        # Leere den Cache, damit die Daten beim nächsten Durchlauf neu geladen werden
        st.cache_data.clear()
        # Führe das Skript erneut aus, um die Tabelle zu aktualisieren
        st.rerun()

# --- Reiter für Lagerbestand und Einkaufsliste ---
tab_lager, tab_einkaufsliste = st.tabs(["Aktueller Lagerbestand", "🛒 Einkaufsliste"])

with tab_lager:
    # --- Suchfunktion ---
    suchbegriff = st.text_input(
        "Artikel suchen...", 
        placeholder="Suche",
        label_visibility="collapsed" # Versteckt das Label "Artikel suchen..."
    )

    # Filtere den DataFrame basierend auf dem Suchbegriff (ignoriert Groß-/Kleinschreibung)
    if suchbegriff:
        gefiltertes_df = inventar_df[inventar_df['name'].str.contains(suchbegriff, case=False, na=False)]
    else:
        gefiltertes_df = inventar_df

    # Sortiere den (gefilterten) DataFrame alphabetisch nach dem Namen
    if not gefiltertes_df.empty:
        gefiltertes_df = gefiltertes_df.sort_values(by='name', ascending=True)

    # --- Anzeige des Inventars ---
    if gefiltertes_df.empty:
        if suchbegriff:
            st.warning(f"Keine Artikel gefunden, die '{suchbegriff}' enthalten.")
        else:
            st.warning("Dein Inventar ist leer. Zeit, etwas hinzuzufügen!")
    else:
        # Erstelle eine benutzerdefinierte Tabelle mit Spaltenüberschriften
        # Diese Kopfzeile ist nur auf breiteren Bildschirmen wirklich nützlich,
        # aber wir behalten sie für die Desktop-Ansicht.
        st.divider() # Fügt eine visuelle Trennlinie hinzu

        # Zeige jeden Artikel in einer eigenen "Karten"-Ansicht an
        for index, row in gefiltertes_df.iterrows():
            col1, col2 = st.columns([5, 2]) # 5 Teile für Infos, 2 für den Button

            # Im ersten Container fassen wir die Infos zusammen
            col1.markdown(f"**{row['name']}**")
            col1.markdown(f"Menge: **{row['menge']} {row['einheit']}**")
            col1.markdown(f"Kategorie: *{row['kategorie']}*")
            
            # Prüfe, ob das Ablaufdatum bald erreicht ist und färbe es ggf. rot ein
            heute = datetime.date.today()
            tage_bis_ablauf = (row['ablaufdatum'] - heute).days
            if tage_bis_ablauf <= 14:
                col1.caption(f"Haltbar bis: :red[{row['ablaufdatum'].strftime('%d.%m.%Y')}] (Läuft bald ab!)")
            else:
                col1.caption(f"Haltbar bis: {row['ablaufdatum'].strftime('%d.%m.%Y')}")

            # Im zweiten Container platzieren wir den Button
            if col2.button("Bearbeiten", key=f"edit_{row['id']}", use_container_width=True):
                st.session_state.editing_id = row['id']
                st.rerun()
            
            st.divider() # Trennlinie nach jedem Artikel

with tab_einkaufsliste:
    st.header("Artikel, die zur Neige gehen")
    # Filtere Artikel, deren Menge < 0.5 ist
    einkaufsliste_df = inventar_df[inventar_df['menge'] < 0.5].copy()

    if einkaufsliste_df.empty:
        st.success("🎉 Super! Deine Vorräte sind gut gefüllt. Die Einkaufsliste ist leer.")
    else:
        st.warning("Diese Artikel solltest du bald nachkaufen:")
        st.dataframe(einkaufsliste_df[['name', 'menge', 'einheit', 'kategorie']], use_container_width=True)
        
# --- Bearbeitungs-Dialog initialisieren (außerhalb der if-Abfrage) ---
@st.dialog("Artikel bearbeiten")
def edit_item_dialog():
    # Finde den zu bearbeitenden Artikel über die Session State ID
    artikel_zum_bearbeiten = inventar_df[inventar_df['id'] == st.session_state.editing_id].iloc[0]
    
    # Das Formular innerhalb des Dialogs
    with st.form("edit_form"):
        st.markdown(f"Du bearbeitest gerade: **{artikel_zum_bearbeiten['name']}**")
        # Finde den Index der aktuellen Einheit
        try:
            einheit_index = EINHEITEN.index(artikel_zum_bearbeiten['einheit'])
        except ValueError:
            einheit_index = 0

        # Eingabefelder (Reihenfolge für schnellere Mengenanpassung optimiert)
        neue_einheit = st.selectbox("Einheit", options=EINHEITEN, index=einheit_index)
        
        if neue_einheit in ganzzahl_einheiten:
            neue_menge = st.number_input("Menge", min_value=0, step=1, value=int(artikel_zum_bearbeiten['menge']))
        else:
            neue_menge = st.number_input("Menge", min_value=0.0, step=0.1, value=float(artikel_zum_bearbeiten['menge']))
        
        neuer_name = st.text_input("Name", value=artikel_zum_bearbeiten['name'])

        try:
            kategorie_index = kategorie_optionen.index(artikel_zum_bearbeiten['kategorie'])
        except (ValueError, KeyError):
            kategorie_index = 0
        neue_kategorie = st.selectbox("Kategorie", options=kategorie_optionen, index=kategorie_index)

        neues_ablaufdatum = st.date_input("Ablaufdatum", value=artikel_zum_bearbeiten['ablaufdatum'])

        neuer_barcode = st.text_input("Barcode (optional)", value=artikel_zum_bearbeiten.get('barcode', ''))

        # Speicher- und Abbrechen-Buttons
        col1, col2 = st.columns(2)
        if col1.form_submit_button("Speichern", use_container_width=True, type="primary"):
             # Finde den Index des Artikels im DataFrame
             idx_to_update = inventar_df[inventar_df['id'] == st.session_state.editing_id].index[0]
             # Aktualisiere die Werte
             inventar_df.loc[idx_to_update, 'name'] = neuer_name
             inventar_df.loc[idx_to_update, 'menge'] = neue_menge
             inventar_df.loc[idx_to_update, 'einheit'] = neue_einheit
             inventar_df.loc[idx_to_update, 'ablaufdatum'] = neues_ablaufdatum
             inventar_df.loc[idx_to_update, 'kategorie'] = neue_kategorie
             inventar_df.loc[idx_to_update, 'barcode'] = neuer_barcode
             
             speichere_daten(CSV_DATEIPFAD, inventar_df)
             st.session_state.editing_id = None
             st.cache_data.clear()
             st.rerun()
        
        if col2.form_submit_button("Abbrechen", use_container_width=True, type="secondary"):
             st.session_state.editing_id = None
             st.rerun()

# --- Dialog aufrufen, wenn eine ID im Session State ist ---
if st.session_state.editing_id:
    edit_item_dialog()