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

TURNI = ("Mattina", "Pomeriggio", "Notte")

# Valori di partenza delle nuove regole (modificabili dalla scheda "Regole")
SCOSTAMENTO_MAX_TURNI = 2  # massimo scostamento dal monte ore mensile, in turni
MAX_TURNI_CONSECUTIVI = 6  # dopo 6 turni di lavoro serve 1 giorno di riposo

# Penalità (in "minuti equivalenti") per il turno assegnato il giorno dopo lo smonto.
# Il pomeriggio è il turno preferito e non ha penalità.
PENALITA_DOPO_SMONTO = {"Mattina": 60, "Pomeriggio": 0, "Notte": 600}


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


# ---------- Monte ore ----------
def monte_ore_minuti(ore_settimanali: float, giorni: int) -> int:
    """Monte ore per un periodo: ore settimanali x (giorni / 7), cioè settimane e frazioni."""
    return int(round(float(ore_settimanali) * 60 * giorni / 7))


def tolleranza_minuti(regole: dict) -> int:
    """Scostamento massimo ammesso dal monte ore mensile: N turni (di mattina) in minuti."""
    turni_ammessi = int(regole.get("scostamento_max_turni", SCOSTAMENTO_MAX_TURNI))
    return turni_ammessi * durata_turno_minuti(regole, "Mattina")


def giorni_obiettivo(anno: int, mese: int, mesi_storico) -> int:
    """Giorni su cui calcolare l'obiettivo progressivo: mesi già validati + mese corrente."""
    giorni = calendar.monthrange(anno, mese)[1]
    return giorni + sum(
        calendar.monthrange(anno, numero)[1] for numero in mesi_storico if numero < mese
    )


def formatta_minuti(minuti: int) -> str:
    segno = "-" if minuti < 0 else ""
    valore = abs(int(minuti))
    return f"{segno}{valore // 60}h{valore % 60:02d}"


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


def _riposo_insufficiente(
    giorno_a: date,
    turno_a: str,
    giorno_b: date,
    turno_b: str,
    orari: dict[str, tuple[str, str]],
    riposo_minuti: int,
) -> bool:
    """True se tra i due turni passano meno ore del riposo minimo (o si sovrappongono)."""
    inizio_a, fine_a = _intervallo_turno(giorno_a, *orari[turno_a])
    inizio_b, fine_b = _intervallo_turno(giorno_b, *orari[turno_b])
    if fine_a <= inizio_b:
        pausa = inizio_b - fine_a
    elif fine_b <= inizio_a:
        pausa = inizio_a - fine_b
    else:
        return True
    return pausa < timedelta(minutes=riposo_minuti)


def _turni_precedenti(
    calendario_precedente: list[dict] | None,
    primo_giorno: date,
    nomi,
) -> dict[tuple[str, date], str]:
    """Turni degli ultimi 7 giorni del mese precedente, per garantire la continuità."""
    risultato: dict[tuple[str, date], str] = {}
    for riga in calendario_precedente or []:
        try:
            giorno = _data(riga["Data"])
        except (KeyError, TypeError, ValueError):
            continue
        nome = str(riga.get("Nome", "")).strip()
        if nome in nomi and primo_giorno - timedelta(days=7) <= giorno < primo_giorno:
            risultato[(nome, giorno)] = str(riga.get("Turno"))
    return risultato


def _genera(
    personale: list[dict],
    regole: dict,
    anno: int,
    mese: int,
    pianificazione: dict,
    storico_minuti: dict[str, int] | None = None,
    giorni_storico: int = 0,
    calendario_precedente: list[dict] | None = None,
    numero_alternative: int = 1,
    secondi_per_alternativa: int = 30,
) -> tuple[list[list[dict]], list[str]]:
    """Genera uno o più calendari mensili rispettando coperture e vincoli individuali.

    Restituisce (lista di calendari, errori): ogni calendario è una lista di turni.
    Le alternative rispettano tutte le regole obbligatorie, hanno una qualità vicina
    alla migliore e differiscono tra loro per almeno il 15% delle assegnazioni.

    Regole obbligatorie:
    - al massimo un turno al giorno e riposo minimo tra un turno e l'altro;
    - dopo la notte c'è lo smonto: il giorno successivo non si lavora (quindi niente
      seconda notte di fila);
    - ogni 6 turni di lavoro almeno 1 giorno di riposo (lo smonto non conta come riposo);
    - il monte ore del mese (ore settimanali x giorni / 7) non può essere superato
      di oltre N turni (scostamento_max_turni, di partenza 2).
    Regole preferenziali, in ordine di importanza:
    - se il personale non basta si riduce per primo il pomeriggio, solo se serve;
    - se non basta, la copertura mattutina feriale può scendere fino a 4;
    - la copertura minima della notte non si riduce mai;
    - dopo lo smonto si preferisce il pomeriggio;
    - il monte ore mensile viene avvicinato il più possibile (anche verso il basso);
    - equità tra le persone.
    """
    turni = TURNI
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
        riposo = int(regole.get("riposo_minimo_ore", 11)) * 60  # in minuti
        max_consecutivi = int(regole.get("max_turni_consecutivi", MAX_TURNI_CONSECUTIVI))
        durate = {turno: durata_turno_minuti(regole, turno) for turno in turni}
        tolleranza = tolleranza_minuti(regole)
    except (KeyError, StopIteration, TypeError, ValueError):
        return [], ["Controlla le regole: mancano orari o valori validi per uno o più turni."]
    if max_consecutivi < 1:
        return [], ["I turni consecutivi prima del riposo devono essere almeno 1."]
    if tolleranza < 0:
        return [], ["Lo scostamento massimo dal monte ore non può essere negativo."]

    giorni = [
        date(anno, mese, giorno)
        for giorno in range(1, calendar.monthrange(anno, mese)[1] + 1)
    ]
    una_giornata = timedelta(days=1)
    prec = _turni_precedenti(calendario_precedente, giorni[0], persone)

    richieste: dict[tuple[date, str], int] = {}
    minimi_richiesti: dict[tuple[date, str], int] = {}
    massimo_scarto_copertura = 0
    for giorno in giorni:
        for turno in turni:
            fascia = "Festivo" if _turno_festivo(giorno, turno, extra) else "Feriale"
            try:
                richiesta = int(coperture.get(turno, {}).get(fascia, 0) or 0)
            except (TypeError, ValueError):
                return [], [f"Copertura non valida per il turno {turno}."]
            if richiesta < 0:
                return [], [f"La copertura del turno {turno} non può essere negativa."]
            richieste[(giorno, turno)] = richiesta
            minimo = richiesta
            # Solo il pomeriggio feriale può scendere sotto la copertura piena
            if turno == "Pomeriggio" and fascia == "Feriale":
                try:
                    minimo = int(regole.get("pomeriggio_feriale_minimo", richiesta))
                except (TypeError, ValueError):
                    return [], ["Il minimo del pomeriggio feriale non è valido."]
                if minimo < 0 or minimo > richiesta:
                    return [], [
                        "Il minimo del pomeriggio feriale deve essere compreso "
                        "tra zero e la copertura feriale configurata."
                    ]
                massimo_scarto_copertura += richiesta - minimo
            elif turno == "Mattina" and fascia == "Feriale":
                minimo = min(richiesta, 4)
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

    def conflitto_con_mese_precedente(nome: str, giorno: date, turno: str) -> str | None:
        for (nome_p, giorno_p), turno_p in prec.items():
            if nome_p != nome:
                continue
            if turno_p == "Notte" and giorno == giorno_p + una_giornata:
                return "smonto"
            if _riposo_insufficiente(giorno_p, turno_p, giorno, turno, orari, riposo):
                return "riposo"
        return None

    # ---------- Controllo dei turni predeterminati ----------
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
        motivo = conflitto_con_mese_precedente(nome, giorno, turno)
        if motivo == "smonto":
            errori.append(
                f"{nome} è in smonto il {giorno:%d/%m/%Y} (notte dell'ultimo giorno del "
                "mese precedente): non può avere un turno predeterminato."
            )
        elif motivo == "riposo":
            errori.append(
                f"Il turno predeterminato di {nome} del {giorno:%d/%m/%Y} non rispetta il "
                "riposo minimo dopo l'ultimo turno del mese precedente."
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

    # Riposo minimo, smonto e giorni consecutivi tra i turni predeterminati della stessa persona
    for nome in persone:
        propri = sorted(
            (giorno, turno) for nome_p, giorno, turno in predeterminati if nome_p == nome
        )
        for indice, (giorno_a, turno_a) in enumerate(propri):
            for giorno_b, turno_b in propri[indice + 1 :]:
                if giorno_a == giorno_b:
                    continue
                if turno_a == "Notte" and giorno_b == giorno_a + una_giornata:
                    errori.append(
                        f"{nome} ha una notte predeterminata il {giorno_a:%d/%m/%Y} e un turno "
                        f"il {giorno_b:%d/%m/%Y}, ma dopo la notte c'è lo smonto."
                    )
                elif _riposo_insufficiente(giorno_a, turno_a, giorno_b, turno_b, orari, riposo):
                    errori.append(
                        f"I turni predeterminati di {nome} del {giorno_a:%d/%m/%Y} "
                        f"e {giorno_b:%d/%m/%Y} non rispettano il riposo minimo."
                    )
        serie = 0
        precedente = None
        for giorno, _ in propri:
            serie = serie + 1 if precedente and giorno == precedente + una_giornata else 1
            if serie == max_consecutivi + 1:
                errori.append(
                    f"{nome} ha più di {max_consecutivi} turni predeterminati consecutivi "
                    f"fino al {giorno:%d/%m/%Y}: serve un giorno di riposo."
                )
            precedente = giorno
    if errori:
        return [], errori

    # ---------- Modello ----------
    from ortools.sat.python import cp_model

    model = cp_model.CpModel()
    assegnazioni = {}
    candidati_per_slot = {}
    termini_copertura_pomeriggio = []
    termini_copertura_mattina = []
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
                if conflitto_con_mese_precedente(nome, giorno, turno):
                    continue
                variabile = model.NewBoolVar(f"x_{len(assegnazioni)}")
                assegnazioni[(nome, giorno, turno)] = variabile
                candidati.append(nome)
                if (nome, giorno, turno) in predet_set:
                    model.Add(variabile == 1)
            candidati_per_slot[slot] = candidati
            if len(candidati) < minimi_richiesti[slot]:
                messaggio = (
                    f"Copertura impossibile il {giorno:%d/%m/%Y} ({turno}): "
                    f"servono almeno {minimi_richiesti[slot]} persone, "
                    f"disponibili {len(candidati)}."
                )
                if turno == "Mattina" and not _turno_festivo(giorno, turno, extra):
                    messaggio += (
                        " La copertura mattutina non può scendere sotto 4. "
                        "Verifica assenze e richieste, abilita o aggiungi personale "
                        "qualificato, oppure valuta una diversa articolazione dei turni."
                    )
                errori.append(messaggio)
            assegnati = sum(assegnazioni[(nome, giorno, turno)] for nome in candidati)
            model.Add(assegnati >= minimi_richiesti[slot])
            model.Add(assegnati <= richieste[slot])
            if richieste[slot] > minimi_richiesti[slot]:
                scopertura = model.NewIntVar(
                    0,
                    richieste[slot] - minimi_richiesti[slot],
                    f"scopertura_{turno.lower()}_{len(termini_copertura_pomeriggio) + len(termini_copertura_mattina)}",
                )
                model.Add(scopertura == richieste[slot] - assegnati)
                if turno == "Pomeriggio":
                    termini_copertura_pomeriggio.append(scopertura)
                elif turno == "Mattina":
                    termini_copertura_mattina.append(scopertura)
    if errori:
        return [], errori

    # Un solo turno al giorno e quote mensili per persona
    for nome in persone:
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

    # Riposo minimo tra un turno e l'altro
    orizzonte_giorni = (max(durate.values()) + riposo) // (24 * 60) + 2
    for nome in persone:
        disponibili = [
            (giorno, turno, assegnazioni[(nome, giorno, turno)])
            for giorno in giorni
            for turno in turni
            if (nome, giorno, turno) in assegnazioni
        ]
        for indice, (giorno_a, turno_a, var_a) in enumerate(disponibili):
            for giorno_b, turno_b, var_b in disponibili[indice + 1 :]:
                if (giorno_b - giorno_a).days > orizzonte_giorni:
                    break
                if giorno_a == giorno_b:
                    continue
                if _riposo_insufficiente(giorno_a, turno_a, giorno_b, turno_b, orari, riposo):
                    model.Add(var_a + var_b <= 1)

    # Smonto: il giorno dopo una notte non si lavora (nemmeno un'altra notte)
    for nome in persone:
        for indice, giorno in enumerate(giorni[:-1]):
            notte = assegnazioni.get((nome, giorno, "Notte"))
            if notte is None:
                continue
            for turno in turni:
                successivo = assegnazioni.get((nome, giorni[indice + 1], turno))
                if successivo is not None:
                    model.Add(notte + successivo <= 1)

    # Ogni `max_consecutivi` turni di lavoro serve 1 giorno di riposo vero:
    # un giorno senza turno e che non sia lo smonto.
    riposi = {}
    for numero_persona, nome in enumerate(persone):
        for indice, giorno in enumerate(giorni):
            riposo_var = model.NewBoolVar(f"riposo_{numero_persona}_{indice}")
            lavoro = [
                assegnazioni[(nome, giorno, turno)]
                for turno in turni
                if (nome, giorno, turno) in assegnazioni
            ]
            if lavoro:
                model.Add(riposo_var + sum(lavoro) <= 1)
            if indice > 0:
                notte_ieri = assegnazioni.get((nome, giorni[indice - 1], "Notte"))
                if notte_ieri is not None:
                    model.Add(riposo_var + notte_ieri <= 1)
            elif prec.get((nome, giorno - una_giornata)) == "Notte":
                model.Add(riposo_var == 0)
            riposi[(nome, indice)] = riposo_var
    lunghezza_finestra = max_consecutivi + 1
    for nome in persone:
        for inizio_finestra in range(-(lunghezza_finestra - 1), len(giorni) - lunghezza_finestra + 1):
            if inizio_finestra + lunghezza_finestra - 1 < 0:
                continue
            gia_soddisfatta = False
            variabili_riposo = []
            for indice in range(inizio_finestra, inizio_finestra + lunghezza_finestra):
                if indice >= 0:
                    variabili_riposo.append(riposi[(nome, indice)])
                    continue
                giorno_prec = giorni[0] + timedelta(days=indice)
                ha_lavorato = (nome, giorno_prec) in prec
                in_smonto = prec.get((nome, giorno_prec - una_giornata)) == "Notte"
                if not ha_lavorato and not in_smonto:
                    gia_soddisfatta = True
                    break
            if not gia_soddisfatta and variabili_riposo:
                model.Add(sum(variabili_riposo) >= 1)

    # ---------- Obiettivo ----------
    termini: list[tuple[object, int, int]] = []  # (espressione, peso, valore massimo)

    # Equità: a ognuno una quota di turni proporzionale alla sua disponibilità
    for turno in turni:
        slot_totali = sum(richieste[(giorno, turno)] for giorno in giorni)
        opportunita = {
            nome: sum(
                1
                for giorno in giorni
                if nome in candidati_per_slot.get((giorno, turno), ())
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
            ideale = round(10 * slot_totali * disponibili / opportunita_totali)
            massimo = 10 * max(len(giorni), slot_totali)
            scarto = model.NewIntVar(0, massimo, f"scarto_equita_{len(termini)}")
            model.Add(scarto >= 10 * conteggio - ideale)
            model.Add(scarto >= ideale - 10 * conteggio)
            termini.append((scarto, 2, massimo))

    # Monte ore mensile: tetto rigido a +N turni, poi avvicinamento al monte ore
    storico_minuti = storico_minuti or {}
    capacita_ruolo = 0
    for nome, persona in persone.items():
        if persona.get("Categoria", "Di ruolo") != "Di ruolo":
            continue
        try:
            ore_settimanali = float(persona.get("Ore settimanali", 38) or 38)
        except (TypeError, ValueError):
            return [], [f"Ore settimanali non valide per {nome}."]
        credito = int(crediti_mese.get(nome, 0))
        obiettivo_mese = monte_ore_minuti(ore_settimanali, len(giorni))
        tetto = max(obiettivo_mese + tolleranza, credito)
        soglia_bassa = min(obiettivo_mese - tolleranza, tetto)
        capacita_ruolo += tetto - credito
        variabili_ore = [
            (assegnazioni[(nome, giorno, turno)], durate[turno])
            for giorno in giorni
            for turno in turni
            if (nome, giorno, turno) in assegnazioni
        ]
        if not variabili_ore:
            continue
        totale = sum(variabile * durata for variabile, durata in variabili_ore) + credito
        model.Add(totale <= tetto)

        # sotto la soglia di -N turni: penalità forte (non sempre evitabile se i turni
        # richiesti sono meno delle ore dovute)
        if soglia_bassa > 0:
            sotto = model.NewIntVar(0, soglia_bassa, f"sotto_soglia_{len(termini)}")
            model.Add(sotto >= soglia_bassa - totale)
            termini.append((sotto, 5, soglia_bassa))

        # scarto dal monte ore del mese
        limite_mese = 100_000
        scarto_mese = model.NewIntVar(0, limite_mese, f"scarto_mese_{len(termini)}")
        model.Add(scarto_mese >= totale - obiettivo_mese)
        model.Add(scarto_mese >= obiettivo_mese - totale)
        termini.append((scarto_mese, 1, limite_mese))

        # saldo progressivo: recupera gli scostamenti dei mesi già validati
        obiettivo_progressivo = monte_ore_minuti(ore_settimanali, giorni_storico + len(giorni))
        limite_saldo = 1_000_000
        scarto_saldo = model.NewIntVar(0, limite_saldo, f"scarto_saldo_{len(termini)}")
        saldo = int(storico_minuti.get(nome, 0)) + totale - obiettivo_progressivo
        model.Add(scarto_saldo >= saldo)
        model.Add(scarto_saldo >= -saldo)
        termini.append((scarto_saldo, 1, limite_saldo))

    # Controllo preliminare: i turni feriali possono essere coperti solo dal personale di ruolo
    minuti_feriali_minimi = sum(
        minimi_richiesti[(giorno, turno)] * durate[turno]
        for giorno in giorni
        for turno in turni
        if not _turno_festivo(giorno, turno, extra)
    )
    if minuti_feriali_minimi > capacita_ruolo:
        return [], [
            "Il personale di ruolo non basta a coprire i turni feriali anche riducendo "
            f"il pomeriggio al minimo: servono almeno {formatta_minuti(minuti_feriali_minimi)}, "
            f"mentre il monte ore del mese, con lo scostamento massimo di "
            f"{int(regole.get('scostamento_max_turni', SCOSTAMENTO_MAX_TURNI))} turni, "
            f"ne consente al massimo {formatta_minuti(capacita_ruolo)}."
        ]

    # Dopo lo smonto si preferisce il pomeriggio
    termini_smonto: list[tuple[object, int, int]] = []
    for nome in persone:
        for indice, giorno in enumerate(giorni):
            giorno_notte = giorno - 2 * una_giornata  # notte, smonto, poi questo giorno
            if indice >= 2:
                notte_origine = assegnazioni.get((nome, giorno_notte, "Notte"))
                if notte_origine is None:
                    continue
            elif prec.get((nome, giorno_notte)) == "Notte":
                notte_origine = None  # notte del mese precedente: è già un fatto
            else:
                continue
            for turno in turni:
                penalita = PENALITA_DOPO_SMONTO[turno]
                variabile = assegnazioni.get((nome, giorno, turno))
                if not penalita or variabile is None:
                    continue
                if notte_origine is None:
                    termini_smonto.append((variabile, penalita, 1))
                else:
                    sgradito = model.NewBoolVar(f"dopo_smonto_{len(termini_smonto)}")
                    model.Add(sgradito >= notte_origine + variabile - 1)
                    termini_smonto.append((sgradito, penalita, 1))

    # Prima si riduce il pomeriggio, poi la mattina; entrambe le riduzioni
    # prevalgono sulle preferenze individuali e sul bilanciamento delle ore.
    tutti_i_termini = termini + termini_smonto
    massimo_resto = sum(peso * massimo for _, peso, massimo in tutti_i_termini)
    peso_copertura_mattina = massimo_resto + 1
    massimo_riduzione_mattina = sum(
        richieste[(giorno, "Mattina")] - minimi_richiesti[(giorno, "Mattina")]
        for giorno in giorni
    )
    peso_copertura_pomeriggio = (
        massimo_resto + massimo_riduzione_mattina * peso_copertura_mattina + 1
    )
    if tutti_i_termini or termini_copertura_pomeriggio or termini_copertura_mattina:
        model.Minimize(
            sum(espressione * peso for espressione, peso, _ in tutti_i_termini)
            + sum(termini_copertura_mattina) * peso_copertura_mattina
            + sum(termini_copertura_pomeriggio) * peso_copertura_pomeriggio
        )

    def risolvi(seme: int):
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = secondi_per_alternativa
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = seme
        return solver, solver.Solve(model)

    def estrai(solver) -> list[dict]:
        risultato = []
        for (nome, giorno, turno), variabile in assegnazioni.items():
            if solver.Value(variabile):
                risultato.append({"Data": giorno.isoformat(), "Turno": turno, "Nome": nome})
        risultato.sort(key=lambda riga: (riga["Data"], turni.index(riga["Turno"]), riga["Nome"]))
        return risultato

    def assegnazioni_attive(solver):
        # i turni predeterminati sono uguali in tutte le proposte: non contano
        return [
            variabile
            for chiave, variabile in assegnazioni.items()
            if solver.Value(variabile) and chiave not in predet_set
        ]

    solver, stato = risolvi(42)
    if stato not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        if stato == cp_model.INFEASIBLE:
            return [], [
                "Non esiste un calendario che rispetti contemporaneamente coperture, "
                "quote mensili, assenze, turni predeterminati, riposo minimo, smonto dopo la "
                f"notte, un giorno di riposo ogni {max_consecutivi} turni e il tetto del monte "
                "ore mensile. Controlla i vincoli del mese: per esempio quote massime troppo "
                "basse, troppe assenze o turni predeterminati ravvicinati. Se non è sufficiente, "
                "verifica la disponibilità e l'abilitazione del personale, ridistribuisci le "
                "richieste o valuta una diversa articolazione dei turni mantenendo almeno 4 "
                "unità al mattino."
            ]
        return [], ["Il calcolo del calendario non è riuscito entro il tempo disponibile."]

    soluzioni = [estrai(solver)]

    if numero_alternative > 1:
        # Le alternative non possono peggiorare la copertura, né aggiungere notti
        # "notte, smonto, notte", e devono restare vicine alla qualità della migliore.
        copertura_migliore = sum(solver.Value(v) for v in termini_copertura_pomeriggio)
        smonto_migliore = sum(solver.Value(e) * peso for e, peso, _ in termini_smonto)
        altri_migliori = sum(solver.Value(e) * peso for e, peso, _ in termini)
        if termini_copertura_pomeriggio:
            model.Add(sum(termini_copertura_pomeriggio) <= copertura_migliore)
        if termini_smonto:
            model.Add(
                sum(e * peso for e, peso, _ in termini_smonto)
                <= smonto_migliore + 2 * PENALITA_DOPO_SMONTO["Mattina"]
            )
        if termini:
            model.Add(
                sum(e * peso for e, peso, _ in termini)
                <= altri_migliori + max(400, altri_migliori // 30)
            )
        precedenti = assegnazioni_attive(solver)
        for numero in range(1, numero_alternative):
            diverse = max(10, int(len(precedenti) * 0.15))
            if len(precedenti) < diverse:
                break
            model.Add(sum(precedenti) <= len(precedenti) - diverse)
            solver, stato = risolvi(42 + numero)
            if stato not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                break  # non esistono altre soluzioni abbastanza diverse
            soluzioni.append(estrai(solver))
            precedenti = assegnazioni_attive(solver)
    return soluzioni, []


def genera_calendario(
    personale: list[dict],
    regole: dict,
    anno: int,
    mese: int,
    pianificazione: dict,
    storico_minuti: dict[str, int] | None = None,
    giorni_storico: int = 0,
    calendario_precedente: list[dict] | None = None,
) -> tuple[list[dict], list[str]]:
    """Genera il calendario migliore (una sola proposta)."""
    soluzioni, errori = _genera(
        personale, regole, anno, mese, pianificazione, storico_minuti,
        giorni_storico, calendario_precedente, numero_alternative=1,
    )
    return (soluzioni[0] if soluzioni else []), errori


def genera_alternative(
    personale: list[dict],
    regole: dict,
    anno: int,
    mese: int,
    pianificazione: dict,
    storico_minuti: dict[str, int] | None = None,
    giorni_storico: int = 0,
    calendario_precedente: list[dict] | None = None,
    numero: int = 3,
    secondi_per_alternativa: int = 30,
) -> tuple[list[list[dict]], list[str]]:
    """Genera fino a `numero` calendari validi e sensibilmente diversi tra loro."""
    return _genera(
        personale, regole, anno, mese, pianificazione, storico_minuti,
        giorni_storico, calendario_precedente,
        numero_alternative=max(1, numero),
        secondi_per_alternativa=secondi_per_alternativa,
    )


def riepilogo_calendario(
    personale: list[dict],
    regole: dict,
    anno: int,
    mese: int,
    pianificazione: dict,
    assegnazioni: list[dict],
    calendario_precedente: list[dict] | None = None,
) -> tuple[dict, list[dict]]:
    """Indicatori per confrontare le proposte e tabella per persona."""
    persone = {
        str(persona.get("Nome", "")).strip(): persona
        for persona in personale
        if str(persona.get("Nome", "")).strip()
    }
    try:
        durate = {turno: durata_turno_minuti(regole, turno) for turno in TURNI}
        tolleranza = tolleranza_minuti(regole)
        extra = {}
        for riga in pianificazione.get("festivi_extra", []):
            if riga.get("Data"):
                extra[_data(riga["Data"])] = str(riga.get("Descrizione") or "")
    except (KeyError, StopIteration, TypeError, ValueError):
        return {}, []
    giorni = [
        date(anno, mese, numero) for numero in range(1, calendar.monthrange(anno, mese)[1] + 1)
    ]
    una_giornata = timedelta(days=1)
    lavoro: dict[tuple[str, date], str] = {}
    pomeriggi_per_giorno: dict[date, int] = {}
    for riga in assegnazioni:
        nome = str(riga.get("Nome", "")).strip()
        giorno = _data(riga["Data"])
        lavoro[(nome, giorno)] = riga["Turno"]
        if riga["Turno"] == "Pomeriggio":
            pomeriggi_per_giorno[giorno] = pomeriggi_per_giorno.get(giorno, 0) + 1
    prec = _turni_precedenti(calendario_precedente, giorni[0], persone)
    tutti = {**prec, **lavoro}

    copertura_pomeriggio = next(
        (r for r in regole.get("copertura", []) if r.get("Turno") == "Pomeriggio"), {}
    )
    pomeriggi_ridotti = 0
    mattine_ridotte = 0
    for giorno in giorni:
        fascia = "Festivo" if _turno_festivo(giorno, "Pomeriggio", extra) else "Feriale"
        try:
            richiesta = int(copertura_pomeriggio.get(fascia, 0) or 0)
        except (TypeError, ValueError):
            richiesta = 0
        if pomeriggi_per_giorno.get(giorno, 0) < richiesta:
            pomeriggi_ridotti += 1
        if not _turno_festivo(giorno, "Mattina", extra):
            richiesta_mattina = next(
                (r for r in regole.get("copertura", []) if r.get("Turno") == "Mattina"), {}
            )
            assegnati_mattina = sum(
                1 for (nome, data), turno in lavoro.items()
                if data == giorno and turno == "Mattina"
            )
            try:
                if assegnati_mattina < int(richiesta_mattina.get("Feriale", 0) or 0):
                    mattine_ridotte += 1
            except (TypeError, ValueError):
                pass

    notte_smonto_notte = sum(
        1
        for (nome, giorno), turno in lavoro.items()
        if turno == "Notte" and tutti.get((nome, giorno - 2 * una_giornata)) == "Notte"
    )

    crediti, _ = crediti_assenza_minuti(pianificazione, regole, anno, mese)
    righe = []
    fuori_limite = 0
    scostamento_massimo = 0.0
    for nome, persona in persone.items():
        propri = [(giorno, turno) for (n, giorno), turno in lavoro.items() if n == nome]
        lavorati = sum(durate[turno] for _, turno in propri)
        credito = crediti.get(nome, 0)
        riga = {
            "Nome": nome,
            "Mattine": sum(1 for _, t in propri if t == "Mattina"),
            "Pomeriggi": sum(1 for _, t in propri if t == "Pomeriggio"),
            "Notti": sum(1 for _, t in propri if t == "Notte"),
            "Festivi": sum(1 for giorno, t in propri if _turno_festivo(giorno, t, extra)),
            "Ore lavorate": formatta_minuti(lavorati),
            "Ore assenza": formatta_minuti(credito),
            "Scostamento (turni)": None,
        }
        if persona.get("Categoria", "Di ruolo") == "Di ruolo":
            try:
                ore_settimanali = float(persona.get("Ore settimanali", 38) or 38)
            except (TypeError, ValueError):
                ore_settimanali = 38.0
            scostamento = lavorati + credito - monte_ore_minuti(ore_settimanali, len(giorni))
            riga["Scostamento (turni)"] = round(scostamento / durate["Mattina"], 1)
            scostamento_massimo = max(scostamento_massimo, abs(scostamento) / durate["Mattina"])
            if abs(scostamento) > tolleranza:
                fuori_limite += 1
        righe.append(riga)
    indicatori = {
        "pomeriggi_ridotti": pomeriggi_ridotti,
        "mattine_ridotte": mattine_ridotte,
        "notte_smonto_notte": notte_smonto_notte,
        "persone_fuori_limite": fuori_limite,
        "scostamento_massimo_turni": round(scostamento_massimo, 1),
    }
    return indicatori, righe


def controlla_calendario(
    personale: list[dict],
    regole: dict,
    anno: int,
    mese: int,
    pianificazione: dict,
    assegnazioni: list[dict],
    calendario_precedente: list[dict] | None = None,
) -> tuple[list[str], list[str]]:
    """Verifica un calendario. Restituisce (violazioni, avvisi).

    Le violazioni sono regole obbligatorie non rispettate; gli avvisi sono
    informazioni utili (pomeriggi ridotti, monte ore fuori tolleranza, ecc.).
    """
    violazioni: list[str] = []
    avvisi: list[str] = []
    persone = {
        str(persona.get("Nome", "")).strip(): persona
        for persona in personale
        if str(persona.get("Nome", "")).strip()
    }
    try:
        orari = {
            riga["Turno"]: (riga["Inizio"], riga["Fine"]) for riga in regole.get("turni", [])
        }
        riposo = int(regole.get("riposo_minimo_ore", 11)) * 60
        max_consecutivi = int(regole.get("max_turni_consecutivi", MAX_TURNI_CONSECUTIVI))
        durate = {turno: durata_turno_minuti(regole, turno) for turno in TURNI}
        tolleranza = tolleranza_minuti(regole)
        extra = {}
        for riga in pianificazione.get("festivi_extra", []):
            if riga.get("Data"):
                extra[_data(riga["Data"])] = str(riga.get("Descrizione") or "")
    except (KeyError, StopIteration, TypeError, ValueError):
        return ["Regole non valide: impossibile controllare il calendario."], []

    giorni = [
        date(anno, mese, numero) for numero in range(1, calendar.monthrange(anno, mese)[1] + 1)
    ]
    una_giornata = timedelta(days=1)
    lavoro: dict[tuple[str, date], str] = {}
    conteggio_slot: dict[tuple[date, str], int] = {}
    for riga in assegnazioni:
        nome = str(riga.get("Nome", "")).strip()
        giorno = _data(riga["Data"])
        turno = riga["Turno"]
        if giorno.year != anno or giorno.month != mese:
            continue
        if (nome, giorno) in lavoro:
            violazioni.append(f"{nome} ha più di un turno il {giorno:%d/%m/%Y}.")
        lavoro[(nome, giorno)] = turno
        conteggio_slot[(giorno, turno)] = conteggio_slot.get((giorno, turno), 0) + 1
    prec = _turni_precedenti(calendario_precedente, giorni[0], persone)
    tutti = {**prec, **lavoro}

    # Riposo minimo e smonto
    for nome in persone:
        propri = sorted((giorno, turno) for (n, giorno), turno in tutti.items() if n == nome)
        for indice, (giorno_a, turno_a) in enumerate(propri):
            for giorno_b, turno_b in propri[indice + 1 :]:
                if (giorno_b - giorno_a).days > 3:
                    break
                if giorno_b < giorni[0]:
                    continue
                if turno_a == "Notte" and giorno_b == giorno_a + una_giornata:
                    violazioni.append(
                        f"{nome}: turno il {giorno_b:%d/%m/%Y} dopo la notte del "
                        f"{giorno_a:%d/%m/%Y} (manca lo smonto)."
                    )
                elif _riposo_insufficiente(giorno_a, turno_a, giorno_b, turno_b, orari, riposo):
                    violazioni.append(
                        f"{nome}: riposo insufficiente tra il {giorno_a:%d/%m/%Y} "
                        f"e il {giorno_b:%d/%m/%Y}."
                    )

    # Un giorno di riposo ogni `max_consecutivi` turni (lo smonto non conta come riposo)
    def e_riposo(nome: str, giorno: date) -> bool:
        return (nome, giorno) not in tutti and tutti.get((nome, giorno - una_giornata)) != "Notte"

    lunghezza = max_consecutivi + 1
    for nome in persone:
        segnalata = False
        for inizio in range(-(lunghezza - 1), len(giorni) - lunghezza + 1):
            if inizio + lunghezza - 1 < 0:
                continue
            primo = giorni[0] + timedelta(days=inizio)
            finestra = [primo + timedelta(days=k) for k in range(lunghezza)]
            if any(e_riposo(nome, giorno) for giorno in finestra):
                segnalata = False
                continue
            if not segnalata:
                violazioni.append(
                    f"{nome}: nessun giorno di riposo tra il {finestra[0]:%d/%m/%Y} e il "
                    f"{finestra[-1]:%d/%m/%Y} (più di {max_consecutivi} turni di lavoro di seguito)."
                )
            segnalata = True

    # Coperture
    pomeriggi_ridotti = []
    mattine_ridotte = []
    for giorno in giorni:
        for turno in TURNI:
            fascia = "Festivo" if _turno_festivo(giorno, turno, extra) else "Feriale"
            try:
                richiesta = int(
                    next(
                        (r for r in regole.get("copertura", []) if r.get("Turno") == turno), {}
                    ).get(fascia, 0)
                    or 0
                )
            except (TypeError, ValueError):
                continue
            assegnati = conteggio_slot.get((giorno, turno), 0)
            if assegnati < richiesta:
                if turno == "Pomeriggio":
                    pomeriggi_ridotti.append(f"{giorno:%d/%m} ({assegnati} su {richiesta})")
                elif turno == "Mattina" and fascia == "Feriale" and assegnati >= min(richiesta, 4):
                    mattine_ridotte.append(f"{giorno:%d/%m} ({assegnati} su {richiesta})")
                else:
                    violazioni.append(
                        f"Copertura incompleta il {giorno:%d/%m/%Y} ({turno}): "
                        f"{assegnati} persone su {richiesta}."
                        + (
                            " La copertura mattutina feriale non può scendere sotto 4; "
                            "verifica assenze e richieste, abilita o aggiungi personale "
                            "qualificato, oppure valuta una diversa articolazione dei turni."
                            if turno == "Mattina" and fascia == "Feriale"
                            else ""
                        )
                    )
    if pomeriggi_ridotti:
        avvisi.append(
            "Personale insufficiente: pomeriggio ridotto nei giorni "
            + ", ".join(pomeriggi_ridotti)
            + "."
        )
    if mattine_ridotte:
        avvisi.append(
            "Personale insufficiente: mattina ridotta, mantenendo almeno 4 unità, nei giorni "
            + ", ".join(mattine_ridotte)
            + "."
        )

    # Notte, smonto, notte: ammesso solo se non se ne può fare a meno
    for (nome, giorno), turno in sorted(lavoro.items(), key=lambda voce: (voce[0][1], voce[0][0])):
        if turno == "Notte" and tutti.get((nome, giorno - 2 * una_giornata)) == "Notte":
            avvisi.append(
                f"{nome} fa una notte il {giorno:%d/%m/%Y} due giorni dopo un'altra notte "
                "(notte, smonto, notte)."
            )

    # Monte ore mensile
    crediti, errori_crediti = crediti_assenza_minuti(pianificazione, regole, anno, mese)
    if not errori_crediti:
        sotto, sopra = [], []
        for nome, persona in persone.items():
            if persona.get("Categoria", "Di ruolo") != "Di ruolo":
                continue
            try:
                ore_settimanali = float(persona.get("Ore settimanali", 38) or 38)
            except (TypeError, ValueError):
                continue
            lavorati = sum(durate[turno] for (n, _), turno in lavoro.items() if n == nome)
            totale = lavorati + crediti.get(nome, 0)
            obiettivo = monte_ore_minuti(ore_settimanali, len(giorni))
            scostamento = totale - obiettivo
            if scostamento > tolleranza and totale > crediti.get(nome, 0):
                sopra.append(f"{nome} ({formatta_minuti(scostamento)})")
            elif scostamento < -tolleranza:
                sotto.append(f"{nome} ({formatta_minuti(scostamento)})")
        if sopra:
            violazioni.append(
                "Monte ore mensile superato di oltre "
                f"{int(regole.get('scostamento_max_turni', SCOSTAMENTO_MAX_TURNI))} turni: "
                + ", ".join(sopra)
                + "."
            )
        if sotto:
            avvisi.append(
                "Sotto il monte ore mensile di oltre "
                f"{int(regole.get('scostamento_max_turni', SCOSTAMENTO_MAX_TURNI))} turni: "
                + ", ".join(sotto)
                + ". I turni richiesti non bastano per far raggiungere a tutti il monte ore."
            )
    return violazioni, avvisi
