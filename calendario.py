import calendar
from datetime import date, datetime, time, timedelta

from dateutil.easter import easter


CODICI_ASSENZA = {
    "Ferie": "F",
    "Riposo": "R",
    "Malattia": "M",
    "Congedo straordinario (CS)": "CS",
    "Congedo straordinario": "CS",
    "Permesso": "CS",
}


def festivita_nazionali(anno: int) -> dict[date, str]:
    fisse = {
        (1, 1): "Capodanno",
        (1, 6): "Epifania",
        (4, 25): "Festa della Liberazione",
        (5, 1): "Festa dei lavoratori",
        (6, 2): "Festa della Repubblica",
        (8, 15): "Ferragosto",
        (11, 1): "Ognissanti",
        (12, 8): "Immacolata Concezione",
        (12, 25): "Natale",
        (12, 26): "Santo Stefano",
    }
    giorni = {date(anno, mese, giorno): nome for (mese, giorno), nome in fisse.items()}
    giorni[easter(anno) + timedelta(days=1)] = "Lunedì dell'Angelo"
    return giorni


def is_festivo(data: date, extra: dict[date, str]) -> bool:
    return data.weekday() == 6 or data in festivita_nazionali(data.year) or data in extra


def _data(valore) -> date:
    if isinstance(valore, datetime):
        return valore.date()
    if isinstance(valore, date):
        return valore
    return date.fromisoformat(str(valore)[:10])


def _intervallo_turno(giorno: date, inizio: str, fine: str) -> tuple[datetime, datetime]:
    inizio_ora = time.fromisoformat(inizio)
    fine_ora = time.fromisoformat(fine)
    inizio_dt = datetime.combine(giorno, inizio_ora)
    fine_dt = datetime.combine(giorno, fine_ora)
    if fine_dt <= inizio_dt:
        fine_dt += timedelta(days=1)
    return inizio_dt, fine_dt


def durata_turno_minuti(regole: dict, turno: str) -> int:
    configurazione = next(
        riga for riga in regole.get("turni", []) if riga.get("Turno") == turno
    )
    inizio, fine = time.fromisoformat(configurazione["Inizio"]), time.fromisoformat(
        configurazione["Fine"]
    )
    inizio_minuti = inizio.hour * 60 + inizio.minute
    fine_minuti = fine.hour * 60 + fine.minute
    return (fine_minuti - inizio_minuti) % (24 * 60) or 24 * 60


def codice_assenza(tipo: str | None) -> str:
    return CODICI_ASSENZA.get(str(tipo), "CS")


def crediti_assenza_minuti(
    pianificazione: dict,
    regole: dict,
    anno: int,
    mese: int,
) -> tuple[dict[str, int], list[str]]:
    """Calcola i crediti assenza previsti, senza attribuire ore ai riposi."""
    ultimo_giorno = calendar.monthrange(anno, mese)[1]
    crediti: dict[str, int] = {}
    errori = []
    viste: set[tuple[str, date]] = set()
    for assenza in pianificazione.get("assenze", []):
        nome = str(assenza.get("Nome", "")).strip()
        if not nome or not assenza.get("Dal") or not assenza.get("Al"):
            continue
        try:
            dal, al = _data(assenza["Dal"]), _data(assenza["Al"])
        except (TypeError, ValueError):
            errori.append(f"L'assenza di {nome} contiene date non valide.")
            continue
        tipo = str(assenza.get("Tipo") or "")
        codice = codice_assenza(tipo)
        turno = str(assenza.get("Turno previsto") or "")
        if al < dal:
            errori.append(f"La data finale dell'assenza di {nome} precede quella iniziale.")
            continue
        if (dal.year, dal.month) != (anno, mese) and (al.year, al.month) != (anno, mese):
            continue
        if codice != "R" and turno not in ("Mattina", "Pomeriggio", "Notte"):
            errori.append(
                f"Indica il turno previsto (Mattina, Pomeriggio o Notte) "
                f"per l'assenza di {nome} ({tipo})."
            )
            continue
        if turno and turno not in ("Mattina", "Pomeriggio", "Notte"):
            errori.append(f"Turno previsto non valido per l'assenza di {nome}.")
            continue
        for giorno_numero in range(1, ultimo_giorno + 1):
            giorno = date(anno, mese, giorno_numero)
            if not dal <= giorno <= al:
                continue
            chiave = (nome, giorno)
            if chiave in viste:
                errori.append(
                    f"{nome} ha più assenze sovrapposte il {giorno:%d/%m/%Y}."
                )
                continue
            viste.add(chiave)
            if codice == "R":
                continue
            try:
                crediti[nome] = crediti.get(nome, 0) + durata_turno_minuti(regole, turno)
            except (KeyError, StopIteration, TypeError, ValueError):
                errori.append(
                    f"Non riesco a calcolare la durata del turno previsto per {nome} "
                    f"il {giorno:%d/%m/%Y}."
                )
    return crediti, errori


def ore_calendario_per_persona(
    personale: list[dict],
    regole: dict,
    pianificazione: dict,
    assegnazioni: list[dict],
    anno: int,
    mese: int,
) -> tuple[dict[str, int], dict[str, int], list[str]]:
    """Restituisce minuti lavorati e minuti accreditati di assenza per persona."""
    lavorati: dict[str, int] = {}
    for assegnazione in assegnazioni:
        nome = str(assegnazione.get("Nome", "")).strip()
        try:
            giorno = _data(assegnazione["Data"])
            if giorno.year != anno or giorno.month != mese:
                continue
            lavorati[nome] = lavorati.get(nome, 0) + durata_turno_minuti(
                regole, assegnazione["Turno"]
            )
        except (KeyError, StopIteration, TypeError, ValueError):
            return {}, {}, [f"Turno salvato non valido per {nome}."]
    assenze, errori = crediti_assenza_minuti(pianificazione, regole, anno, mese)
    if errori:
        return {}, {}, errori
    nomi = {
        str(persona.get("Nome", "")).strip()
        for persona in personale
        if str(persona.get("Nome", "")).strip()
    }
    if set(lavorati) - nomi or set(assenze) - nomi:
        return {}, {}, ["Il calendario o le assenze contengono persone non presenti nel personale."]
    return lavorati, assenze, []


def _turno_festivo(giorno: date, turno: str, extra: dict[date, str]) -> bool:
    return is_festivo(giorno, extra) or (
        turno == "Notte" and is_festivo(giorno + timedelta(days=1), extra)
    )


def genera_calendario(
    personale: list[dict],
    regole: dict,
    anno: int,
    mese: int,
    pianificazione: dict,
    storico_minuti: dict[str, int] | None = None,
    assegnazioni_precedenti: list[dict] | None = None,
) -> tuple[list[dict], list[str]]:
    """Genera il calendario mensile rispettando coperture e vincoli individuali."""
    turni = ("Mattina", "Pomeriggio", "Notte")
    persone = {
        str(persona.get("Nome", "")).strip(): persona
        for persona in personale
        if str(persona.get("Nome", "")).strip()
    }
    extra = {}
    try:
        for riga in pianificazione.get("festivi_extra", []):
            if riga.get("Data"):
                extra[_data(riga["Data"])] = str(riga.get("Descrizione") or "Festività locale")
    except (TypeError, ValueError):
        return [], ["C'è una festività locale con data non valida."]

    assenze: dict[str, list[tuple[datetime, datetime]]] = {nome: [] for nome in persone}
    for assenza in pianificazione.get("assenze", []):
        nome = str(assenza.get("Nome", "")).strip()
        if not assenza.get("Dal") or not assenza.get("Al"):
            continue
        if nome not in persone:
            return [], [f"L'assenza indica una persona non presente: {nome}."]
        try:
            dal, al = _data(assenza["Dal"]), _data(assenza["Al"])
        except (TypeError, ValueError):
            return [], [f"L'assenza di {nome} contiene date non valide."]
        if al < dal:
            return [], [f"La data finale dell'assenza di {nome} precede quella iniziale."]
        assenze[nome].append(
            (datetime.combine(dal, time.min), datetime.combine(al + timedelta(days=1), time.min))
        )
    crediti_mese, errori_crediti = crediti_assenza_minuti(pianificazione, regole, anno, mese)
    if errori_crediti:
        return [], errori_crediti
    for nome in crediti_mese:
        if nome not in persone:
            return [], [f"L'assenza indica una persona non presente: {nome}."]

    configurati = {
        riga["Turno"]: riga
        for riga in regole.get("turni", [])
        if riga.get("Turno") in turni
    }
    coperture = {
        riga["Turno"]: riga
        for riga in regole.get("copertura", [])
        if riga.get("Turno") in turni
    }
    try:
        orari = {
            turno: (
                configurati[turno]["Inizio"],
                configurati[turno]["Fine"],
            )
            for turno in turni
        }
        for inizio, fine in orari.values():
            time.fromisoformat(inizio)
            time.fromisoformat(fine)
        riposo = int(regole.get("riposo_minimo_ore", 11)) * 60
    except (KeyError, TypeError, ValueError):
        return [], ["Controlla le regole: mancano orari validi per uno o più turni."]

    giorni = [
        date(anno, mese, giorno)
        for giorno in range(1, calendar.monthrange(anno, mese)[1] + 1)
    ]
    giorno_inizio_mese = giorni[0]
    turno_precedente: set[tuple[str, date, str]] = set()
    for riga in assegnazioni_precedenti or []:
        nome = str(riga.get("Nome", "")).strip()
        try:
            giorno = _data(riga["Data"])
        except (KeyError, TypeError, ValueError):
            return [], ["Il calendario precedente contiene una data non valida."]
        turno = riga.get("Turno")
        if nome not in persone:
            return [], [f"Il calendario precedente indica una persona non presente: {nome}."]
        if turno not in turni:
            return [], [f"Il calendario precedente contiene un turno non valido per {nome}."]
        if giorno < giorno_inizio_mese and giorno >= giorno_inizio_mese - timedelta(days=6):
            turno_precedente.add((nome, giorno, turno))

    richieste: dict[tuple[date, str], int] = {}
    minimi_richiesti: dict[tuple[date, str], int] = {}
    termini_copertura_pomeriggio = []
    for giorno in giorni:
        for turno in turni:
            fascia = "Festivo" if _turno_festivo(giorno, turno, extra) else "Feriale"
            try:
                richiesta = int(coperture[turno].get(fascia, 0) or 0)
            except (TypeError, ValueError):
                return [], [f"Copertura non valida per il turno {turno}."]
            if richiesta < 0:
                return [], [f"La copertura del turno {turno} non può essere negativa."]
            richieste[(giorno, turno)] = richiesta
            minimo = 0 if turno == "Pomeriggio" else richiesta
            minimi_richiesti[(giorno, turno)] = minimo

    quote = {
        str(riga.get("Nome", "")).strip(): riga
        for riga in pianificazione.get("quote", [])
        if str(riga.get("Nome", "")).strip()
    }
    limiti: dict[tuple[str, str], tuple[int, int]] = {}
    errori = []
    for nome in persone:
        for turno, prefisso in zip(turni, ("M", "P", "N")):
            riga = quote.get(nome, {})
            try:
                minimo = int(riga.get(f"{prefisso} min") or 0)
                valore_massimo = riga.get(f"{prefisso} max")
                massimo = len(giorni) if valore_massimo is None else int(valore_massimo)
            except (TypeError, ValueError):
                errori.append(f"Quote non valide per {nome} ({turno}).")
                continue
            if minimo < 0 or massimo < 0 or minimo > massimo:
                errori.append(f"Quote minime/massime non valide per {nome} ({turno}).")
            limiti[(nome, turno)] = (minimo, massimo)
    if errori:
        return [], errori

    predeterminati = []
    for riga in pianificazione.get("predeterminati", []):
        nome = str(riga.get("Nome", "")).strip()
        try:
            giorno = _data(riga["Data"])
        except (KeyError, TypeError, ValueError):
            return [], ["C'è un turno predeterminato con data non valida."]
        turno = riga.get("Turno")
        if nome not in persone:
            errori.append(f"Il turno predeterminato indica una persona non presente: {nome}.")
        elif turno not in turni:
            errori.append(f"Turno non valido nel predeterminato di {nome} del {giorno:%d/%m/%Y}.")
        elif giorno not in giorni:
            errori.append(f"Turno predeterminato fuori dal mese: {nome} ({giorno:%d/%m/%Y}).")
        else:
            predeterminati.append((nome, giorno, turno))
    if errori:
        return [], errori

    predet_set = set(predeterminati)
    if len(predet_set) != len(predeterminati):
        return [], ["Ci sono turni predeterminati duplicati."]
    for nome, giorno, turno in predeterminati:
        persona = persone[nome]
        inizio, fine = _intervallo_turno(giorno, *orari[turno])
        if not bool(persona.get(turno, False)):
            errori.append(f"{nome} non è abilitato al turno {turno.lower()} predeterminato.")
        if persona.get("Categoria") == "Solo festivi" and not _turno_festivo(giorno, turno, extra):
            errori.append(
                f"{nome} è disponibile solo nei festivi ma ha un turno predeterminato "
                f"feriale il {giorno:%d/%m/%Y}."
            )
        if any(
            assenza_inizio < fine and inizio < assenza_fine
            for assenza_inizio, assenza_fine in assenze[nome]
        ):
            errori.append(
                f"{nome} ha un turno predeterminato durante un'assenza "
                f"il {giorno:%d/%m/%Y} ({turno.lower()})."
            )
    predet_per_slot: dict[tuple[date, str], int] = {}
    predet_per_persona_giorno: dict[tuple[str, date], int] = {}
    for nome, giorno, turno in predeterminati:
        slot = (giorno, turno)
        predet_per_slot[slot] = predet_per_slot.get(slot, 0) + 1
        persona_giorno = (nome, giorno)
        predet_per_persona_giorno[persona_giorno] = (
            predet_per_persona_giorno.get(persona_giorno, 0) + 1
        )
        if predet_per_slot[slot] > richieste[slot]:
            errori.append(
                f"I turni predeterminati superano la copertura del {giorno:%d/%m/%Y} ({turno})."
            )
    for (nome, giorno), numero_turni in predet_per_persona_giorno.items():
        if numero_turni > 1:
            errori.append(
                f"{nome} ha più di un turno predeterminato il {giorno:%d/%m/%Y}; "
                "è consentito un solo turno al giorno."
            )
    for nome, giorno, turno in predeterminati:
        if turno == "Notte" and (nome, giorno + timedelta(days=1)) in predet_per_persona_giorno:
            errori.append(
                f"{nome} deve avere lo smonto il {giorno + timedelta(days=1):%d/%m/%Y} "
                "dopo la notte predeterminata."
            )
    for nome in persone:
        for giorno_finale in giorni:
            giorni_lavorati = {
                giorno_storico
                for nome_storico, giorno_storico, _ in turno_precedente
                if nome_storico == nome
                and giorno_finale - timedelta(days=6) <= giorno_storico < giorno_inizio_mese
            }
            giorni_lavorati.update(
                giorno_predeterminato
                for nome_predeterminato, giorno_predeterminato in predet_per_persona_giorno
                if nome_predeterminato == nome
                and giorno_finale - timedelta(days=6)
                <= giorno_predeterminato
                <= giorno_finale
            )
            if len(giorni_lavorati) > 6:
                errori.append(
                    f"{nome} supera sei giorni lavorati nella finestra di sette giorni "
                    f"che termina il {giorno_finale:%d/%m/%Y}."
                )
        if any(
            nome_storico == nome
            and giorno_storico == giorno_inizio_mese - timedelta(days=1)
            and turno_storico == "Notte"
            for nome_storico, giorno_storico, turno_storico in turno_precedente
        ) and (nome, giorno_inizio_mese) in predet_per_persona_giorno:
            errori.append(
                f"{nome} deve avere lo smonto il {giorno_inizio_mese:%d/%m/%Y} "
                "dopo la notte del mese precedente."
            )
    if errori:
        return [], errori

    from ortools.sat.python import cp_model

    model = cp_model.CpModel()
    assegnazioni = {}
    candidati_per_slot = {}
    for giorno in giorni:
        for turno in turni:
            slot = (giorno, turno)
            if not richieste[slot]:
                continue
            inizio, fine = _intervallo_turno(giorno, *orari[turno])
            candidati = []
            for nome, persona in persone.items():
                if not bool(persona.get(turno, False)):
                    continue
                if persona.get("Categoria") == "Solo festivi" and not _turno_festivo(
                    giorno, turno, extra
                ):
                    continue
                if any(
                    assenza_inizio < fine and inizio < assenza_fine
                    for assenza_inizio, assenza_fine in assenze[nome]
                ):
                    continue
                variabile = model.NewBoolVar(f"x_{len(assegnazioni)}")
                assegnazioni[(nome, giorno, turno)] = variabile
                candidati.append(nome)
                if (nome, giorno, turno) in predet_set:
                    model.Add(variabile == 1)
            candidati_per_slot[slot] = candidati
            if len(candidati) < minimi_richiesti[slot]:
                errori.append(
                    f"Copertura impossibile il {giorno:%d/%m/%Y} ({turno}): "
                    f"servono almeno {minimi_richiesti[slot]} persone, "
                    f"disponibili {len(candidati)}."
                )
            assegnati = sum(assegnazioni[(nome, giorno, turno)] for nome in candidati)
            model.Add(assegnati >= minimi_richiesti[slot])
            model.Add(assegnati <= richieste[slot])
            if turno == "Pomeriggio" and richieste[slot] > minimi_richiesti[slot]:
                scopertura = model.NewIntVar(
                    0,
                    richieste[slot],
                    f"scopertura_pomeriggio_{len(termini_copertura_pomeriggio)}",
                )
                model.Add(scopertura == richieste[slot] - assegnati)
                termini_copertura_pomeriggio.append(scopertura)
    if errori:
        return [], errori

    for nome, persona in persone.items():
        for giorno in giorni:
            turni_giorno = [
                assegnazioni[(nome, giorno, turno)]
                for turno in turni
                if (nome, giorno, turno) in assegnazioni
            ]
            if turni_giorno:
                model.Add(sum(turni_giorno) <= 1)
            notte = assegnazioni.get((nome, giorno, "Notte"))
            giorno_successivo = giorno + timedelta(days=1)
            if notte is not None and giorno_successivo in giorni:
                turni_successivi = [
                    assegnazioni[(nome, giorno_successivo, turno)]
                    for turno in turni
                    if (nome, giorno_successivo, turno) in assegnazioni
                ]
                if turni_successivi:
                    model.Add(notte + sum(turni_successivi) <= 1)

        for giorno_finale in giorni:
            finestra = [
                giorno_finale - timedelta(days=offset)
                for offset in range(6, -1, -1)
            ]
            turni_finestra = []
            turni_storici = 0
            for giorno_finestra in finestra:
                if giorno_finestra < giorno_inizio_mese:
                    turni_storici += any(
                        nome_storico == nome and data_storica == giorno_finestra
                        for nome_storico, data_storica, _ in turno_precedente
                    )
                    continue
                turni_finestra.extend(
                    assegnazioni[(nome, giorno_finestra, turno)]
                    for turno in turni
                    if (nome, giorno_finestra, turno) in assegnazioni
                )
            if turni_finestra:
                model.Add(sum(turni_finestra) + turni_storici <= 6)

        notte_precedente = any(
            nome_storico == nome
            and data_storica == giorno_inizio_mese - timedelta(days=1)
            and turno_storico == "Notte"
            for nome_storico, data_storica, turno_storico in turno_precedente
        )
        if notte_precedente:
            turni_primo_giorno = [
                assegnazioni[(nome, giorno_inizio_mese, turno)]
                for turno in turni
                if (nome, giorno_inizio_mese, turno) in assegnazioni
            ]
            if turni_primo_giorno:
                model.Add(sum(turni_primo_giorno) == 0)

        for turno in turni:
            variabili = [
                variabile
                for (persona, _, tipo), variabile in assegnazioni.items()
                if persona == nome and tipo == turno
            ]
            minimo, massimo = limiti[(nome, turno)]
            if variabili:
                model.Add(sum(variabili) >= minimo)
                model.Add(sum(variabili) <= massimo)
            elif minimo:
                errori.append(f"{nome} ha un minimo di {minimo} turni {turno.lower()}, ma nessuno è assegnabile.")
    if errori:
        return [], errori

    for nome in persone:
        disponibili = [
            (giorno, turno, assegnazioni[(nome, giorno, turno)])
            for giorno in giorni
            for turno in turni
            if (nome, giorno, turno) in assegnazioni
        ]
        for indice, (giorno_a, turno_a, var_a) in enumerate(disponibili):
            inizio_a, fine_a = _intervallo_turno(giorno_a, *orari[turno_a])
            for giorno_b, turno_b, var_b in disponibili[indice + 1 :]:
                inizio_b, fine_b = _intervallo_turno(giorno_b, *orari[turno_b])
                if fine_a <= inizio_b:
                    riposo_insufficiente = (inizio_b - fine_a).total_seconds() < riposo * 60
                elif fine_b <= inizio_a:
                    riposo_insufficiente = (inizio_a - fine_b).total_seconds() < riposo * 60
                else:
                    riposo_insufficiente = True
                if riposo_insufficiente:
                    model.Add(var_a + var_b <= 1)

    termini_obiettivo = []
    massimo_scarto_equita = 0
    for turno in turni:
        slot_totali = sum(richieste[(giorno, turno)] for giorno in giorni)
        opportunita = {
            nome: sum(
                (giorno, turno) in candidati_per_slot
                and nome in candidati_per_slot[(giorno, turno)]
                for giorno in giorni
            )
            for nome in persone
        }
        opportunita_totali = sum(opportunita.values())
        if not opportunita_totali:
            continue
        for nome, disponibili in opportunita.items():
            if not disponibili:
                continue
            conteggio = sum(
                assegnazioni[(nome, giorno, turno)]
                for giorno in giorni
                if (nome, giorno, turno) in assegnazioni
            )
            scarto = model.NewIntVar(0, slot_totali * opportunita_totali, f"scarto_{len(termini_obiettivo)}")
            massimo_scarto_equita += slot_totali * opportunita_totali
            differenza = model.NewIntVar(
                -slot_totali * opportunita_totali,
                slot_totali * opportunita_totali,
                f"differenza_{len(termini_obiettivo)}",
            )
            model.Add(differenza == conteggio * opportunita_totali - slot_totali * disponibili)
            model.AddAbsEquality(scarto, differenza)
            termini_obiettivo.append(scarto)
    termini_saldo_ore = []
    storico_minuti = storico_minuti or {}
    giorno_fine_mese = giorni[-1]
    giorni_anno = 366 if calendar.isleap(anno) else 365
    giorno_progressivo = giorno_fine_mese.timetuple().tm_yday
    for nome, persona in persone.items():
        if persona.get("Categoria", "Di ruolo") != "Di ruolo":
            continue
        try:
            ore_settimanali = persona.get("Ore settimanali")
            if ore_settimanali is None:
                ore_settimanali = 38
            ore_settimanali = float(ore_settimanali)
            contratto_annuo_minuti = int(round(float(ore_settimanali) * 52 * 60))
        except (TypeError, ValueError):
            return [], [f"Ore settimanali non valide per {nome}."]
        if contratto_annuo_minuti < 0:
            return [], [f"Ore settimanali non valide per {nome}."]
        try:
            durata_turno_diurno = durata_turno_minuti(regole, "Mattina")
        except (KeyError, StopIteration, TypeError, ValueError):
            return [], ["Non riesco a calcolare la durata del turno diurno."]
        obiettivo_mensile = round(ore_settimanali * len(giorni) / 7 * 60)
        tolleranza_mensile = 2 * durata_turno_diurno
        obiettivo_progressivo = round(
            contratto_annuo_minuti * giorno_progressivo / giorni_anno
        )
        ore_assegnate = sum(
            assegnazioni[(nome, giorno, turno)] * durata_turno_minuti(regole, turno)
            for giorno in giorni
            for turno in turni
            if (nome, giorno, turno) in assegnazioni
        )
        model.Add(
            ore_assegnate + int(crediti_mese.get(nome, 0))
            >= max(0, obiettivo_mensile - tolleranza_mensile)
        )
        model.Add(
            ore_assegnate + int(crediti_mese.get(nome, 0))
            <= obiettivo_mensile + tolleranza_mensile
        )
        differenza = model.NewIntVar(-1_000_000, 1_000_000, f"saldo_{len(termini_saldo_ore)}")
        scarto = model.NewIntVar(0, 1_000_000, f"scarto_saldo_{len(termini_saldo_ore)}")
        model.Add(
            differenza
            == int(storico_minuti.get(nome, 0))
            + int(crediti_mese.get(nome, 0))
            + ore_assegnate
            - obiettivo_progressivo
        )
        model.AddAbsEquality(scarto, differenza)
        termini_saldo_ore.append(scarto)

    if termini_obiettivo or termini_saldo_ore or termini_copertura_pomeriggio:
        peso_equita = 1
        peso_saldo_ore = massimo_scarto_equita + 1
        massimo_scarto_saldo = 1_000_000 * len(termini_saldo_ore)
        peso_copertura = massimo_scarto_saldo * peso_saldo_ore + massimo_scarto_equita + 1
        model.Minimize(
            sum(termini_copertura_pomeriggio) * peso_copertura
            + sum(termini_saldo_ore) * peso_saldo_ore
            + sum(termini_obiettivo) * peso_equita
        )

    for indice, (nome_a, giorno_a, turno_a) in enumerate(predeterminati):
        for nome_b, giorno_b, turno_b in predeterminati[indice + 1 :]:
            if nome_a != nome_b:
                continue
            if giorno_a == giorno_b:
                continue
            inizio_a, fine_a = _intervallo_turno(giorno_a, *orari[turno_a])
            inizio_b, fine_b = _intervallo_turno(giorno_b, *orari[turno_b])
            if fine_a <= inizio_b:
                riposo_insufficiente = (inizio_b - fine_a).total_seconds() < riposo * 60
            elif fine_b <= inizio_a:
                riposo_insufficiente = (inizio_a - fine_b).total_seconds() < riposo * 60
            else:
                riposo_insufficiente = True
            if riposo_insufficiente:
                errori.append(
                    f"I turni predeterminati di {nome_a} del {giorno_a:%d/%m/%Y} "
                    f"e {giorno_b:%d/%m/%Y} non rispettano il riposo minimo."
                )
    if errori:
        return [], errori

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 30
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 42
    stato = solver.Solve(model)
    if stato not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        if stato == cp_model.INFEASIBLE:
            return [], [
                "Non esiste un calendario che rispetti contemporaneamente coperture, "
                "quote mensili, assenze, turni predeterminati e riposo minimo. "
                "Controlla i vincoli del mese."
            ]
        return [], ["Il calcolo del calendario non è riuscito entro il tempo disponibile."]

    risultato = []
    for (nome, giorno, turno), variabile in assegnazioni.items():
        if solver.Value(variabile):
            risultato.append(
                {
                    "Data": giorno.isoformat(),
                    "Turno": turno,
                    "Nome": nome,
                }
            )
    risultato.sort(key=lambda riga: (riga["Data"], turni.index(riga["Turno"]), riga["Nome"]))
    return risultato, []
