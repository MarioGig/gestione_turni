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
    richieste: dict[tuple[date, str], int] = {}
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
            if len(candidati) < richieste[slot]:
                errori.append(
                    f"Copertura impossibile il {giorno:%d/%m/%Y} ({turno}): "
                    f"servono {richieste[slot]} persone, disponibili {len(candidati)}."
                )
            model.Add(
                sum(assegnazioni[(nome, giorno, turno)] for nome in candidati)
                == richieste[slot]
            )
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
            contratto_annuo_minuti = int(
                round(float(persona.get("Ore settimanali", 38) or 38) * 52 * 60)
            )
        except (TypeError, ValueError):
            return [], [f"Ore settimanali non valide per {nome}."]
        obiettivo_progressivo = round(
            contratto_annuo_minuti * giorno_progressivo / giorni_anno
        )
        ore_assegnate = sum(
            assegnazioni[(nome, giorno, turno)] * durata_turno_minuti(regole, turno)
            for giorno in giorni
            for turno in turni
            if (nome, giorno, turno) in assegnazioni
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

    if termini_obiettivo or termini_saldo_ore:
        model.Minimize(
            sum(termini_saldo_ore) * 10_000 + sum(termini_obiettivo)
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
