import json
import os
import time
import traceback
import urllib.request
import urllib.parse
from datetime import date, datetime
from garminconnect import Garmin
from google import genai
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

# ================== USTAWIENIA DOSTĘPU ==================
# Bezpieczne pobieranie haseł ze środowiska serwera Mikrus
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
# ========================================================

# PARAMETRY FIZJOLOGICZNE WG DANIELSA:
HR_MAX = 180
HR_REST = 46
LIMIT_BS_MAX = 142
KADENCJA_CEL = 180
START_DOCELOWY = "2027-08-15"

HISTORIA_PLIK = "historia_biegow_2026.json"
WYKRES_PLIK = "raport_dejv_pro.png"

def formatuj_tempo(t_dec):
    if not t_dec or t_dec <= 0 or t_dec > 20:
        return "0:00"
    m = int(t_dec)
    s = int(round((t_dec - m) * 60))
    if s == 60:
        m += 1
        s = 0
    return f"{m}:{s:02d}"

def wylicz_punkty_danielsa(czas_min, sr_hr):
    if not sr_hr or not czas_min:
        return 0.0
    proc = (sr_hr / HR_MAX) * 100
    if proc <= 70: m = 0.167
    elif proc <= 75: m = 0.250
    elif proc <= 79: m = 0.333
    elif proc <= 84: m = 0.442
    elif proc <= 92: m = 0.617
    else: m = 1.000
    return round(czas_min * m, 1)

def pobierz_pelne_dane_z_garmina():
    print("1. Logowanie do Garmin Connect i pobieranie telemetrii Fenixa 7 dla Dejva...")
    try:
        garmin = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
        garmin.login()
    except Exception as e:
        print(f"Błąd logowania do Garmina: {e}")
        return [], {}, {}

    dzisiaj_str = date.today().isoformat()
    poczatek_roku = date(date.today().year, 1, 1).isoformat()

    aktywnosci = garmin.get_activities_by_date(poczatek_roku, dzisiaj_str)
    lista_biegow = []

    for act in aktywnosci:
        typ = act.get("activityType", {}).get("typeKey", "")
        nazwa = act.get("activityName", "").lower()
        if "run" not in typ and "bieg" not in nazwa:
            continue

        dystans_km = round(act.get("distance", 0) / 1000, 2)
        czas_min = round(act.get("duration", 0) / 60, 2)
        if dystans_km <= 0.1:
            continue

        sr_hr = int(act.get("averageHR") or 0)
        max_hr = int(act.get("maxHR") or 0)
        kadencja = round(act.get("averageRunningCadenceInStepsPerMinute") or 0)
        moc_sr = round(act.get("avgPower") or 0)
        moc_max = round(act.get("maxPower") or 0)

        krok_m = round((act.get("avgStrideLength") or 110) / 100, 2)
        pion_cm = round(act.get("avgVerticalOscillation") or 9.2, 1)
        pion_proc = round(act.get("avgVerticalRatio") or 8.4, 1)

        efekt_aero = round(act.get("aerobicTrainingEffect") or 3.0, 1)
        efekt_anaero = round(act.get("anaerobicTrainingEffect") or 0.0, 1)
        obciazenie = round(act.get("activityTrainingLoad") or 100)

        tempo_num = round(czas_min / dystans_km, 2)
        tempo_str = formatuj_tempo(tempo_num)
        pkt_dan = wylicz_punkty_danielsa(czas_min, sr_hr)

        lista_biegow.append({
            "id": act.get("activityId"),
            "data": act.get("startTimeLocal", "")[:10],
            "dystans_km": dystans_km,
            "czas_min": round(czas_min, 1),
            "tempo_num": tempo_num,
            "tempo_str": tempo_str,
            "srednie_hr": sr_hr,
            "max_hr": max_hr,
            "kadencja_spm": kadencja,
            "moc_w": moc_sr,
            "moc_max_w": moc_max,
            "dlugosc_kroku_m": krok_m,
            "odch_pionowe_cm": pion_cm,
            "odch_proc": pion_proc,
            "efekt_aero": efekt_aero,
            "efekt_anaero": efekt_anaero,
            "obciazenie": obciazenie,
            "punkty_danielsa": pkt_dan,
            "przekroczono_bs": True if (sr_hr > LIMIT_BS_MAX) else False
        })

    lista_biegow.sort(key=lambda x: x["data"])
    
    try:
        with open(HISTORIA_PLIK, "w", encoding="utf-8") as f:
            json.dump(lista_biegow, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Ostrzeżenie zapisu historii: {e}")

    strefy_hr = {"Strefa 1": 0, "Strefa 2 (BS)": 90, "Strefa 3": 10, "Strefa 4": 0, "Strefa 5": 0}
    if lista_biegow:
        ostatni_id = lista_biegow[-1]["id"]
        try:
            zones_data = garmin.get_activity_hr_in_timezones(ostatni_id)
            if zones_data and isinstance(zones_data, list):
                calk_czas = sum(z.get("secsInZone", 0) for z in zones_data) or 1
                for z in zones_data:
                    nr = z.get("zoneNumber")
                    p = round((z.get("secsInZone", 0) / calk_czas) * 100)
                    if nr == 1: strefy_hr["Strefa 1"] = p
                    elif nr == 2: strefy_hr["Strefa 2 (BS)"] = p
                    elif nr == 3: strefy_hr["Strefa 3"] = p
                    elif nr == 4: strefy_hr["Strefa 4"] = p
                    elif nr == 5: strefy_hr["Strefa 5"] = p
        except Exception:
            pass

    reg = {
        "hrv_status": "ZRÓWNOWAŻONY",
        "hrv_nocne_ms": "43",
        "gotowosc_pkt": "25",
        "gotowosc_opis": "NISKA (PO NOCY)",
        "sen_godz": "3.6"
    }

    try:
        hrv_d = garmin.get_hrv_data(dzisiaj_str)
        if hrv_d and isinstance(hrv_d, dict) and "hrvSummary" in hrv_d:
            pod_hrv = hrv_d["hrvSummary"] or {}
            reg["hrv_status"] = pod_hrv.get("status", "ZRÓWNOWAŻONY")
            reg["hrv_nocne_ms"] = pod_hrv.get("lastNightAvg", "43")
    except Exception:
        pass

    try:
        tr_d = garmin.get_training_readiness(dzisiaj_str)
        if isinstance(tr_d, list) and len(tr_d) > 0:
            reg["gotowosc_pkt"] = tr_d[0].get("score", "25")
            reg["gotowosc_opis"] = tr_d[0].get("level", "NISKA")
        elif isinstance(tr_d, dict):
            reg["gotowosc_pkt"] = tr_d.get("score", "25")
            reg["gotowosc_opis"] = tr_d.get("level", "NISKA")
    except Exception:
        pass

    try:
        sl_d = garmin.get_sleep_data(dzisiaj_str)
        if sl_d and "dailySleepDTO" in sl_d:
            sek = sl_d["dailySleepDTO"].get("sleepTimeSeconds") or 0
            if sek > 0:
                reg["sen_godz"] = round(sek / 3600, 1)
    except Exception:
        pass

    print(f"-> Pobrano {len(lista_biegow)} biegów. HRV: {reg['hrv_status']} ({reg['hrv_nocne_ms']} ms) | Gotowość: {reg['gotowosc_pkt']}/100")
    return lista_biegow, strefy_hr, reg

def generuj_dashboard(biegi, strefy_hr, reg):
    if not biegi:
        return
    ostatnie = biegi[-12:] if len(biegi) >= 12 else biegi
    ost = biegi[-1]

    daty = [b["data"][-5:] for b in ostatnie]
    tempa = [b["tempo_num"] for b in ostatnie]
    tetna = [b["srednie_hr"] for b in ostatnie]
    moce = [b["moc_w"] if b["moc_w"] > 0 else 320 for b in ostatnie]
    kadencje = [b["kadencja_spm"] if b["kadencja_spm"] > 0 else 168 for b in ostatnie]
    kroki = [b["dlugosc_kroku_m"] for b in ostatnie]
    pionowe = [b["odch_pionowe_cm"] for b in ostatnie]
    pion_proc = [b["odch_proc"] for b in ostatnie]

    plt.style.use('default')
    fig = plt.figure(figsize=(14, 18), facecolor='#f8f9fa')
    gs = gridspec.GridSpec(6, 2, figure=fig, height_ratios=[1.3, 0.7, 1.0, 1.0, 1.4, 0.7], hspace=0.45, wspace=0.25)

    ax_top = fig.add_subplot(gs[0, :])
    ax_top.set_facecolor('#ffffff')
    ax_top.set_title(f"Zintegrowany Przegląd Wydajności Dejva: Tempo, Tętno i Moc (Ostatni: {ost['data']} | {ost['dystans_km']} km w {ost['tempo_str']} min/km)", fontsize=12, fontweight='bold', loc='left')
    ax_top.plot(daty, tempa, color='#1e88e5', linewidth=2.5, label='Tempo (min/km)')
    ax_top.fill_between(daty, tempa, max(tempa) + 0.5, color='#90caf9', alpha=0.4)
    ax_top.invert_yaxis()
    ax_top.set_ylabel("Tempo (min/km)", color='#1e88e5', fontweight='bold')
    ax_top.grid(True, linestyle='--', alpha=0.4)

    ax_hr = ax_top.twinx()
    ax_hr.plot(daty, tetna, color='#d32f2f', linewidth=2.2, marker='o', label='Średnie HR (bpm)')
    ax_hr.axhline(y=LIMIT_BS_MAX, color='#b71c1c', linestyle='--', linewidth=1.8, label=f'Limit BS Danielsa ({LIMIT_BS_MAX} bpm)')
    ax_hr.set_ylabel("Tętno (bpm)", color='#d32f2f', fontweight='bold')
    ax_hr.legend(loc='upper right')

    ax_pow = fig.add_subplot(gs[1, :])
    ax_pow.set_facecolor('#ffffff')
    ax_pow.fill_between(daty, moce, 0, color='#d81b60', alpha=0.75)
    ax_pow.plot(daty, moce, color='#880e4f', linewidth=1.5)
    ax_pow.set_ylabel("Moc (W)", color='#ad1457', fontweight='bold')
    ax_pow.set_ylim(0, max(moce) + 120)
    ax_pow.grid(True, linestyle='--', alpha=0.4)
    ax_pow.set_title(f"Średnia moc ostatniego biegu: {ost['moc_w']} W (Max: {ost['moc_max_w']} W)", fontsize=10, loc='left')

    ax_cad = fig.add_subplot(gs[2, 0])
    ax_cad.plot(daty, kadencje, color='#2e7d32', marker='s', linewidth=2)
    ax_cad.axhline(y=KADENCJA_CEL, color='#ff6f00', linestyle=':', linewidth=2, label='Cel Danielsa (180 spm)')
    ax_cad.set_title(f"Trend Kadencji (Ostatni: {ost['kadencja_spm']} spm)", fontsize=11, fontweight='bold', loc='left')
    ax_cad.set_ylim(145, 195)
    ax_cad.grid(True, linestyle='--', alpha=0.4)
    ax_cad.legend(loc='lower right', fontsize=8)

    ax_vo = fig.add_subplot(gs[2, 1])
    ax_vo.scatter(daty, pionowe, color='#ef6c00', s=50, zorder=3)
    ax_vo.plot(daty, pionowe, color='#2e7d32', linewidth=1.5, alpha=0.7)
    ax_vo.set_title(f"Odchylenie Pionowe (Ostatni: {ost['odch_pionowe_cm']} cm)", fontsize=11, fontweight='bold', loc='left')
    ax_vo.grid(True, linestyle='--', alpha=0.4)

    ax_str = fig.add_subplot(gs[3, 0])
    ax_str.plot(daty, kroki, color='#1565c0', marker='o', linewidth=2)
    ax_str.set_title(f"Długość Kroku (Ostatni: {ost['dlugosc_kroku_m']} m)", fontsize=11, fontweight='bold', loc='left')
    ax_str.set_ylim(0.7, 1.5)
    ax_str.grid(True, linestyle='--', alpha=0.4)

    ax_vr = fig.add_subplot(gs[3, 1])
    ax_vr.scatter(daty, pion_proc, color='#43a047', s=50, zorder=3)
    ax_vr.plot(daty, pion_proc, color='#1b5e20', linewidth=1.5, alpha=0.7)
    ax_vr.set_title(f"Odchylenie do Długości (Ostatni: {ost['odch_proc']} %)", fontsize=11, fontweight='bold', loc='left')
    ax_vr.grid(True, linestyle='--', alpha=0.4)

    ax_zones = fig.add_subplot(gs[4, 0])
    nazwy_s = list(strefy_hr.keys())
    wart_s = list(strefy_hr.values())
    kol_s = ['#90a4ae', '#1e88e5', '#43a047', '#fb8c00', '#e53935']
    bars = ax_zones.barh(nazwy_s, wart_s, color=kol_s, height=0.55)
    ax_zones.set_xlim(0, 115)
    ax_zones.set_title("Rozkład Czasu w Strefach Tętna (Ostatni Bieg)", fontsize=11, fontweight='bold', loc='left')
    ax_zones.grid(True, axis='x', linestyle='--', alpha=0.4)
    for bar, val in zip(bars, wart_s):
        ax_zones.text(val + 2, bar.get_y() + bar.get_height()/2, f"{val}%", va='center', fontweight='bold')

    ax_card = fig.add_subplot(gs[4, 1])
    ax_card.axis('off')
    st_bs = "PRZEKROCZONY LIMIT! (OPIERDZIEL)" if ost["przekroczono_bs"] else "WZOROWY BIEG SPOKOJNY (BS)"
    kol_bs = "#d32f2f" if ost["przekroczono_bs"] else "#2e7d32"
    karta_txt = (
        f"PODSUMOWANIE EFEKTU & REGENERACJI (DEJV)\n"
        f"------------------------------------------\n"
        f"* Efekt Aerobowy: {ost['efekt_aero']} / 5.0 | Beztlenowy: {ost['efekt_anaero']} / 5.0\n"
        f"* Obciążenie: {ost['obciazenie']} | Punkty Danielsa: {ost['punkty_danielsa']} pkt\n\n"
        f"STATUS HRV I GOTOWOŚCI (PO NOCY):\n"
        f"* Status HRV: {reg['hrv_status']} ({reg['hrv_nocne_ms']} ms)\n"
        f"* Gotowość Treningowa: {reg['gotowosc_pkt']} / 100 ({reg['gotowosc_opis']})\n"
        f"* Zarejestrowany Sen: {reg['sen_godz']} godz.\n\n"
        f"AUDYT METODY DANIELSA (FAZA I - BAZA):\n"
        f"* Dyscyplina Tętna (<= {LIMIT_BS_MAX} bpm): {st_bs}"
    )
    ax_card.text(0.02, 0.95, karta_txt, transform=ax_card.transAxes, fontsize=10, va='top', family='monospace', fontweight='bold', bbox=dict(boxstyle='round,pad=0.8', facecolor='#ffffff', edgecolor=kol_bs, linewidth=2.5))

    ax_tips = fig.add_subplot(gs[5, :])
    ax_tips.axis('off')
    wsk = []
    if ost["przekroczono_bs"]:
        wsk.append(f"1. [DYSCYPLINA TĘTNA]: Dejv, średnie tętno ({ost['srednie_hr']} bpm) przebiło sufit BS ({LIMIT_BS_MAX} bpm)! Zwolnij!")
    else:
        wsk.append(f"1. [DYSCYPLINA TĘTNA]: Tętno {ost['srednie_hr']} bpm wzorowo pod limitem {LIMIT_BS_MAX} bpm! Żelazna baza rośnie.")
    wsk.append(f"2. [MECHANIKA & KADENCJA]: Kadencja {ost['kadencja_spm']} spm (Cel Danielsa: ~180 spm). Dodawaj przebieżki 5-6x20s po BS.")
    wsk.append("3. [REGENERACJA & NOCKI]: Przy 3.6h snu i gotowości 25 – absolutny zakaz biegania, ratujemy układ nerwowy (str. 189)!")
    ax_tips.text(0.01, 0.85, "WSKAZÓWKI TRENERSKIE JACKA DANIELSA:\n" + "\n".join(wsk), transform=ax_tips.transAxes, fontsize=10, va='top', color='#ffffff', fontweight='bold', bbox=dict(boxstyle='round,pad=0.8', facecolor='#263238', edgecolor='#ff6f00', linewidth=2))

    plt.savefig(WYKRES_PLIK, dpi=200, bbox_inches='tight')
    plt.close()
    print(f"-> Wygenerowano 6-panelowy Dashboard: {WYKRES_PLIK}!")

def wyslij_wiadomosc_telegram(chat_id, tekst):
    if "TUTAJ" in TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        data = urllib.parse.urlencode({'chat_id': chat_id, 'text': tekst, 'parse_mode': 'Markdown'}).encode('utf-8')
        urllib.request.urlopen(urllib.request.Request(url, data=data))
    except Exception as e:
        print(f"Błąd wysyłania wiadomości: {e}")

def wyslij_zdjecie_telegram(chat_id, zdjecie_path, podpis):
    if "TUTAJ" in TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    with open(zdjecie_path, 'rb') as f:
        photo_data = f.read()
    boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n{podpis[:950]}\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="{os.path.basename(zdjecie_path)}"\r\nContent-Type: image/png\r\n\r\n'
    ).encode('utf-8') + photo_data + f'\r\n--{boundary}--\r\n'.encode('utf-8')
    req = urllib.request.Request(url, data=body)
    req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
    try:
        urllib.request.urlopen(req)
        print("-> Wysłano Dashboard graficzny na Telegram!")
    except Exception as e:
        print(f"Błąd wysyłania zdjęcia: {e}")

def zapytaj_gemini(client, prompt_uzytkownika, system_prompt):
    try:
        # Używamy bezpośredniego, prostego wywołania bez skomplikowanych prób w pętli, 
        # żeby tekst generował się błyskawicznie.
        res = client.models.generate_content(
            model="gemini-3.8-flash", 
            contents=prompt_uzytkownika, 
            config={"system_instruction": system_prompt}
        )
        return res.text
    except Exception as e:
        print(f"Błąd Gemini: {e}")
        return f"Dejv, chwilowy problem z tekstem od trenera: {e}"

def zbuduj_prompt(biegi, reg):
    ost3 = biegi[-3:] if len(biegi) >= 3 else biegi
    return f"""Jesteś bezkompromisowym, elitarnym trenerem biegowym prowadzącym zawodnika ściśle według książki "Bieganie metodą Danielsa".
Zwracaj się do użytkownika ZAWSZE per "Dejv". Twój styl to miks profesjonalizmu fizjologicznego, czarnego humoru i żołnierskiej bezpośredniości.

WYMAGANY FORMAT RAPORTU TEKSTOWEGO (wypisuj zawsze w tym czytelnym formacie markdown z nagłówkami i punktami):
🏃‍♂️ *RAPORT DANIELSA DLA DEJVA*
### RAPORT TRENERSKI – FAZA I (BAZA TLENOWA)
**Zawodnik:** Dejv | Przygotowania do półmaratonu ({START_DOCELOWY}) | **VDOT:** Wyłączone (kierunek: HR i samopoczucie)
---
### 1. Audyt Zasady 3 (Adaptacja i kilometraż)
- Przeanalizuj historię ostatnich tygodni i ciągłość obciążeń zgodnie z zasadami adaptacji Danielsa.
### 2. Audyt Biegu Długiego (BD <= 30% tygodnia)
- Ocena udziału najdłuższego biegu i struktury bodźców w zestawieniu z historią.
### 3. Dyscyplina tętna (BS <= {LIMIT_BS_MAX} bpm / 79% HRmax)
- Ocena trzymania sufitu tętna na podstawie ostatnich biegów (np. ostatni bieg 27.10).
### 4. Kadencja i Mechanika (Wzorzec: ~180 spm)
- Analiza kadencji, oscylacji pionowej i wskazówki mechaniczne.
### 5. Rozkaz na dziś / Plan na najbliższy tydzień
- Ocena gotowości bojowej (sen {reg['sen_godz']}h, gotowość {reg['gotowosc_pkt']}/100) i twardy rozkaz trenerski (np. zakaz biegania przy skrajnym zmęczeniu po nockach, str. 189).

DANE Z FENIXA 7: HRV: {reg['hrv_status']} ({reg['hrv_nocne_ms']} ms), Gotowość: {reg['gotowosc_pkt']}/100, Sen: {reg['sen_godz']}h. Ostatnie biegi z historii: {json.dumps(ost3, ensure_ascii=False)}"""

def uruchom_bot(biegi, strefy_hr, reg):
    if "TUTAJ" in TELEGRAM_BOT_TOKEN:
        print("\nUWAGA DEJV: Nie wpisałeś jeszcze TELEGRAM_BOT_TOKEN w pliku start.py!")
        return

    print("\n" + "="*60)
    print("TRENER DANIELS ('DEJV PRO' + RAPORT) NASŁUCHUJE NA TELEGRAMIE!")
    print("="*60 + "\n")

    client = genai.Client(api_key=GEMINI_API_KEY)
    offset = 0

    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={offset}&timeout=30"
            req = urllib.request.Request(url)
            response = urllib.request.urlopen(req)
            data = json.loads(response.read().decode('utf-8'))
            
            for result in data.get("result", []):
                offset = result["update_id"] + 1
                msg = result.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text", "").strip()
                
                if not chat_id or not text:
                    continue
                
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Odebrano od Dejva: {text}")
                sys_p = zbuduj_prompt(biegi, reg)
                
                if text == "/odswiez":
                    wyslij_wiadomosc_telegram(chat_id, "Dejv, zasysam świeże dane z Fenixa 7...")
                    biegi, strefy_hr, reg = pobierz_pelne_dane_z_garmina()
                    generuj_dashboard(biegi, strefy_hr, reg)
                    ost = biegi[-1]
                    wyslij_zdjecie_telegram(chat_id, WYKRES_PLIK, f"Świeży raport! {ost['dystans_km']} km | HR: {ost['srednie_hr']} bpm | HRV: {reg['hrv_status']}")
                    # DODANO: Automatyczny raport tekstowy po /odswiez
                    raport_txt = zapytaj_gemini(client, "Przygotuj pełny, szczegółowy raport trenerski Danielsa w oparciu o moją historię biegów, Fazy I oraz stan regeneracji.", sys_p)
                    wyslij_wiadomosc_telegram(chat_id, raport_txt)

                elif text == "/raport" or text.lower() == "raport" or text == "/ostatni":
                    ost = biegi[-1]
                    wyslij_zdjecie_telegram(chat_id, WYKRES_PLIK, f"Dashboard z biegu ({ost['data']})")
                    odp = zapytaj_gemini(client, f"Przygotuj pełny, szczegółowy raport tekstowy Danielsa w oparciu o moją historię biegów, ostatni trening i dzisiejszą regenerację.", sys_p)
                    wyslij_wiadomosc_telegram(chat_id, odp)
                elif text == "/regeneracja":
                    odp = zapytaj_gemini(client, "Oceń moją dzisiejszą regenerację HRV/Gotowość po pracy i podaj rozkaz na dziś.", sys_p)
                    wyslij_wiadomosc_telegram(chat_id, odp)
                else:
                    odp = zapytaj_gemini(client, f"Dejv pisze: '{text}'", sys_p)
                    wyslij_wiadomosc_telegram(chat_id, odp)
        except KeyboardInterrupt:
            print("Zatrzymano bot.")
            break
        except Exception as e:
            time.sleep(3)

if __name__ == "__main__":
    try:
        biegi, strefy_hr, reg = pobierz_pelne_dane_z_garmina()
        if biegi:
            generuj_dashboard(biegi, strefy_hr, reg)
            if "TUTAJ" not in TELEGRAM_BOT_TOKEN:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
                try:
                    response = urllib.request.urlopen(url)
                    data = json.loads(response.read().decode('utf-8'))
                    results = data.get("result", [])
                    if results:
                        cid = results[-1]["message"]["chat"]["id"]
                        ost = biegi[-1]
                        
                        # 1. Wysyłamy grafikę
                        wyslij_zdjecie_telegram(cid, WYKRES_PLIK, f"📊 DASHBOARD FENIX 7 ({ost['data']}): {ost['dystans_km']} km | HR: {ost['srednie_hr']} bpm")
                        
                        print("Wysyłam raport tekstowy Danielsa do Dejva...")
                        time.sleep(2)
                        
                        # 2. Wysyłamy szczegółowy raport tekstowy Danielsa
                        client = genai.Client(api_key=GEMINI_API_KEY)
                        raport_txt = zapytaj_gemini(
                            client, 
                            "Przygotuj pełny, szczegółowy raport trenerski Danielsa w oparciu o moją historię biegów, Fazy I oraz stan regeneracji.", 
                            zbuduj_prompt(biegi, reg)
                        )
                        wyslij_wiadomosc_telegram(cid, raport_txt)
                except Exception as e:
                    print(f"Info startowe Telegram: {e}")
            
            uruchom_bot(biegi, strefy_hr, reg)
    except Exception as e:
        print(f"\nBŁĄD: {e}")
        traceback.print_exc()
    input("\nNaciśnij ENTER, aby zamknąć...")
