"""The car post generator: the Telegram post of a car in the exact format of its channel.

Two formats, as the agency writes them by hand:
  sale -> CarsSaleInGeorgia: «🚘 Model | year💰$price❗️#Exclusive», engine / transmission / mileage, ✨ OPTIONS,
          🔧 CONDITION (+ the description, ✅ maintenance, cosmetic imperfections), 🛡 SAFETY, 🔎 VIN, after-sale
          services, the contact and three lines of search hashtags (brand, body, decade, fuel, engine, mileage, price);
  rent -> CarsRentInGeorgia: «#Brand Model 🚙 #Class📍 pick-up address», #DailyRent #year | gearbox fuel, fuel use and
          the option groups, pets / capacity / trunk, the price per day with the insurances, ✔️Add Services, the terms,
          the contact, free pick-up and «CARS ON MAP».
The hashtags follow the channels' «FULL HASHTAG SYSTEM» post (brands, classes, #Price0to50…#Price200Plus) and the sale
channel's posts (#PriceUnder5000 #Price5000to10000…, #From50000to100000 #From200000, #2010s). Empty fields are left
out, as the old HTML generators did.

Columns of `cars` carry what the site filters on (title, year, price, gearbox, fuel, body, seats, mileage, pickup,
deposit, period, is_exclusive, description = the condition text of a sale post); everything else is post_fields
(FIELDS below). The importer (services/car_import.py) reads the same format back."""
import re

PHONE = "+995 599 20 67 16"
TG_USER = "@David_Tibelashvili"
CHANNELS = {"sale": "CarsSaleInGeorgia", "rent": "CarsRentInGeorgia"}

# column values -> the word in the post / its hashtag
FUEL = {"petrol": "Petrol", "diesel": "Diesel", "hybrid": "Hybrid", "plugin_hybrid": "PlugInHybrid", "electric": "Electric",
        "gas": "Gas"}
BODY = {"sedan": "Sedan", "suv": "SUV", "crossover": "Crossover", "hatchback": "Hatchback", "wagon": "Wagon",
        "coupe": "Coupe", "convertible": "Convertible", "minivan": "Minivan", "cargo_van": "CargoVan", "pickup": "Pickup",
        "business": "BusinessClass"}
# sale: the exact transmission (the column keeps auto | manual for the site's filter)
TRANSMISSION = {"Automatic": "auto", "Manual": "manual", "DCT": "auto", "AutomatedManual": "auto", "CVT": "auto",
                "Robot": "auto"}
DRIVE = ("4WD", "AWD", "FWD", "RWD", "2WD")
CONDITION = ("Excellent", "Good", "Fair", "NeedsRepair")
COLORS = ("Black", "White", "Silver", "Gray", "Blue", "Red", "Green", "Brown", "Beige", "Yellow", "Orange", "Gold",
          "Purple")
SALE_OPTIONS = ("AC", "ClimateControl", "DualClimate", "RearviewCamera", "360Camera", "ParkingSensors",
                "MultifunctionSteering", "Sunroof", "Panorama", "HeatedSeats", "VentilatedSeats", "HeatedSteering",
                "LeatherSeats", "ElectricSeats", "EcoMode", "SportMode", "MultimediaSystem", "Display", "Navigation",
                "Bluetooth", "CarPlay", "AndroidAuto", "LightSensor", "RainSensor", "CruiseControl", "AdaptiveCruise",
                "KeylessEntry", "StartStop", "AlloyWheels", "LEDHeadlights", "BlindSpot", "LaneAssist")
SAFETY = ("ABS", "ESP", "Airbags", "TractionControl", "HillAssist", "ISOFIX", "TPMS", "EmergencyBraking")
# rent: the option groups of the rent post, in its order (🧊 climate, 📲 media, 🛞 drive and assistants)
RENT_OPTIONS = {
    "🧊": ("AC", "ClimateControl", "DualClimate", "HeatedSeats", "HeatedSteering", "VentilatedSeats"),
    "📲": ("Bluetooth", "Multimedia", "USB", "AppleCarPlay", "AndroidAuto", "Navigation", "TouchScreen"),
    "🛞": ("CruiseControl", "AdaptiveCruise", "EcoMode", "SportMode", "RearCamera", "360Camera", "ParkingSensors",
          "Panorama", "Sunroof", "KeylessEntry", "StartStop", "AlloyWheels", "LargeTrunk"),
}
CAR_CLASS = ("EconomyClass", "ComfortClass", "BusinessClass", "PremiumClass", "LuxuryClass")
PETS = {"allowed": "Allowed", "no": "NotAllowed", "agreement": "ByAgreement"}
# ✔️Add Services: key -> (emoji, hashtag); the price is per day
EXTRAS = {"child_seat": ("👶", "ChildSeatAvailable"), "booster": ("💺", "BoosterSeat"), "chains": ("🔗", "SnowChains"),
          "winter_tires": ("❄️", "WinterTires"), "second_driver": ("👥", "SecondDriver"),
          "with_driver": ("🧑‍✈️", "WithDriver"), "sim": ("📶", "SIMCardIncluded")}
DEFAULT_EXTRAS = [{"key": k, "price": 3} for k in ("child_seat", "booster", "chains", "winter_tires", "second_driver")]
DEFAULT_DELIVERY = "Tbilisi / Airport / City Center"
AFTER_SALE = "We can assist with maintenance, repairs, detailing, insurance, and delivery after purchase"

# post_fields the generator reads (services/offers.FIELDS lets the form write exactly these)
FIELDS = {
    "sale": ("engine", "hp", "transmission", "drive", "color", "options", "safety", "condition", "maintenance",
             "cosmetic", "vin", "after_sale"),
    "rent": ("drive", "car_class", "fuel_use", "options", "smoking", "pets", "trunk", "insurance_basic",
             "insurance_full", "extras", "unlimited_mileage", "delivery"),
}
ALL_FIELDS = tuple(dict.fromkeys(FIELDS["sale"] + FIELDS["rent"]))

# brands written in two words / with a dash, and the channel's tag for them
BRANDS = {"mercedes-benz": "Mercedes", "mercedes benz": "Mercedes", "mercedes": "Mercedes", "land rover": "LandRover",
          "alfa romeo": "AlfaRomeo", "rolls-royce": "RollsRoyce", "rolls royce": "RollsRoyce", "aston martin": "AstonMartin",
          "bmw": "BMW", "byd": "BYD", "vw": "Volkswagen", "gmc": "GMC", "mini": "Mini", "kia": "Kia"}

# sale channel buckets: lower end inclusive, upper exclusive ($30,000 -> #Price30000to50000 only), except that the
# «Under» bucket includes its bound — the channel tags $5,000 with both #PriceUnder5000 and #Price5000to10000
SALE_PRICE = ((None, 5000, "PriceUnder5000"), (5000, 10000, "Price5000to10000"), (10000, 20000, "Price10000to20000"),
              (20000, 30000, "Price20000to30000"), (30000, 50000, "Price30000to50000"),
              (50000, 100000, "Price50000to100000"), (100000, None, "Price100000plus"))
MILEAGE = ((None, 50000, "Under50000"), (50000, 100000, "From50000to100000"), (100000, 150000, "From100000to150000"),
           (150000, 200000, "From150000to200000"), (200000, None, "From200000"))
RENT_PRICE = ((0, 50, "Price0to50"), (50, 100, "Price50to100"), (100, 150, "Price100to150"), (150, 200, "Price150to200"),
              (200, None, "Price200Plus"))


def _f(c, key, default=None):
    v = (c.post_fields or {}).get(key)
    return default if v in (None, "", []) else v


def _num(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _int(v) -> str:
    n = _num(v)
    return "" if n is None else (str(int(n)) if n == int(n) else f"{n:g}")


def _thousands(v) -> str:
    return f"{int(_num(v)):,}"


def _tag(word: str) -> str:
    return "#" + re.sub(r"[^\w]", "", str(word))


def split_brand(title: str) -> tuple[str, str]:
    """«Mercedes-Benz CLA 250» -> ("Mercedes", "CLA 250"); the first word otherwise (dash and dots dropped)."""
    t = (title or "").strip()
    low = t.lower()
    for name, tag in sorted(BRANDS.items(), key=lambda x: -len(x[0])):
        if low == name or low.startswith(name + " "):
            return tag, t[len(name):].strip()
    first, _, rest = t.partition(" ")
    return re.sub(r"[^\w]", "", first), rest.strip()


def buckets(value, table) -> list[str]:
    n = _num(value)
    if n is None:
        return []
    return [tag for lo, hi, tag in table if (lo is None or n >= lo) and (hi is None or n < hi or lo is None and n == hi)]


def money(c, amount, sale: bool) -> str:
    """Sale: «$5,000» before the number; rent: «60$» after it (both as in the channels)."""
    sign = {"USD": "$", "GEL": "₾", "EUR": "€"}.get(c.currency or "USD", "$")
    return f"{sign}{_thousands(amount)}" if sale else f"{_int(amount)}{sign}"


def engine_tag(engine: str) -> str:
    m = re.match(r"\s*([IVWH]\d{1,2}|Boxer|Rotary)\b", engine or "", re.I)
    return m[1].upper() if m and m[1][0].lower() in "ivwh" else (m[1].title() if m else "")


def _usd_price(c) -> float | None:
    p = _num(c.price)
    if p is None or (c.currency or "USD") == "USD":
        return p
    from app.services.site_feed import rates  # NBG rates, USD per unit (cached 12 h)
    fx = rates().get(c.currency)
    return p * fx if fx else p


def sale_post(c) -> str:
    brand, model = split_brand(c.title)
    fuel = FUEL.get(c.fuel or "", "")
    body = BODY.get(c.body or "", "")
    head = f"🚘 {c.title.strip()}" + (f" | {c.year}" if c.year else "")
    if c.price is not None:
        head += f"💰{money(c, c.price, True)}"
    if c.is_exclusive:
        head += "❗️#Exclusive"
    out = [head, "0% Commission", ""]
    engine, hp = _f(c, "engine", ""), _f(c, "hp")
    gear = _f(c, "transmission") or {"auto": "Automatic", "manual": "Manual"}.get(c.gearbox or "")
    drive = _f(c, "drive")
    motor = " · ".join(x for x in (fuel, engine, hp and f"{_int(hp)} HP") if x)
    trans = " · ".join(_tag(x) for x in (gear, drive) if x)
    line = " ".join(x for x in (motor and f"⛽️ {motor}", trans and f"⚙️ Transmission: {trans}") if x)
    if line:
        out.append(line + " ")
    color = _f(c, "color")
    road = " · ".join(x for x in (c.mileage is not None and f"{_thousands(c.mileage)}km", body) if x)
    line = " ".join(x for x in (road and f"🛣 {road}", color and f"🎨 {_tag(color)}") if x)
    if line:
        out.append(line)
    if opts := _f(c, "options"):
        out += ["", "✨ OPTIONS", " · ".join(_tag(o) for o in opts)]
    if cond := _f(c, "condition"):
        out += ["", f"🔧 CONDITION: {_tag(cond)}"]
    if (c.description or "").strip():
        out += ["", c.description.strip()]
    if fixes := [x.strip() for x in _f(c, "maintenance", []) if str(x).strip()]:
        out += [""] + [f"✅ {x}" for x in fixes]
    if marks := [x.strip() for x in _f(c, "cosmetic", []) if str(x).strip()]:
        out += ["", "Cosmetic imperfections:"] + [f"• {x}" for x in marks]
    if safety := _f(c, "safety"):
        out += ["", "🛡 SAFETY", " · ".join(_tag(o) for o in safety)]
    if vin := _f(c, "vin"):
        out += ["", f"🔎 VIN: {str(vin).strip().upper()}"]
    if _f(c, "after_sale", True) is not False:
        out += ["", "🛠 AFTER-SALE SERVICES", AFTER_SALE]
    out += ["", f"📲 {PHONE}", f"👤 {TG_USER}", ""]
    decade = f"{c.year // 10 * 10}s" if c.year else ""
    out.append(" ".join(_tag(x) for x in (brand, body, decade) if x) + " ")
    tags = [fuel, engine_tag(engine)] + buckets(c.mileage, MILEAGE) + buckets(_usd_price(c), SALE_PRICE)
    out.append(" ".join(_tag(x) for x in tags if x))
    out.append("#CarsSaleInGeorgia #CarsRentInGeorgia")
    return _tidy(out)


def rent_post(c) -> str:
    brand, model = split_brand(c.title)
    klass = _f(c, "car_class") or BODY.get(c.body or "", "")
    head = " ".join(x for x in (_tag(brand) if brand else "", model) if x) + " 🚙"
    if klass:
        head += f" {_tag(klass)}"
    if c.pickup:
        head += f"📍 {c.pickup.strip()}"
    out = [head, ""]
    period = "#MonthlyRent" if c.period == "month" else "#DailyRent"
    gear = {"auto": "Automatic", "manual": "Manual"}.get(c.gearbox or "")
    fuel = FUEL.get(c.fuel or "", "")
    line = " ".join(x for x in (period, c.year and f"#{c.year}") if x)
    rest = " ".join(x for x in (gear and _tag(gear), fuel and f"⛽ {_tag(fuel)}") if x)
    out += [f"🛣️ {line}" + (f" | {rest}" if rest else "") + " ", ""]
    opts = set(_f(c, "options", []))
    parts = [f"🪫 Fuel Use: {_int(fu)} L / 100 km" for fu in [_f(c, "fuel_use")] if fu]
    for emoji, group in RENT_OPTIONS.items():
        picked = [o for o in group if o in opts]
        if emoji == "🛞" and _f(c, "drive"):
            picked.insert(0, _f(c, "drive"))
        if picked:
            parts.append(emoji + " " + " ".join(_tag(o) for o in picked))
    smoking = _f(c, "smoking", False)
    parts.append("🚬 Smoking ✅" if smoking is True else "🚫 Smoking ❌")
    out += [" ".join(parts), ""]
    pets = _f(c, "pets")
    if pets:
        out.append(f"🐶 Pets: {_tag(PETS.get(pets, pets))}  ")
    cap = " | ".join(x for x in (c.seats and f"Capacity: {c.seats} seats", _f(c, "trunk") and f"Trunk: {_int(_f(c, 'trunk'))} L") if x)
    if cap:
        out.append(f"👨‍👩‍👦 {cap}  ")
    if pets or cap:
        out.append("")
    per = "month" if c.period == "month" else "day"
    if c.price is not None:
        out.append(f"💰 {money(c, c.price, False)}/ {per}  ")
    if c.deposit:
        out.append(f"Deposit: {money(c, c.deposit, False)}")
    for key, name in (("insurance_basic", "Basic Insurance"), ("insurance_full", "Full Insurance")):
        if (v := _f(c, key)) is not None:
            out.append(f"{name}: {money(c, v, False)}")
    extras = [e for e in _f(c, "extras", []) if isinstance(e, dict) and e.get("key") in EXTRAS]
    if extras:
        out += ["", "✔️Add Services"]
        for e in extras:
            emoji, tag = EXTRAS[e["key"]]
            price = _num(e.get("price"))
            out.append(f"{emoji}#{tag}" + (f": {money(c, price, False)}/day" if price else ": Free"))
    out.append("")
    if c.is_exclusive:
        out.append("❗️#Exclusive")
    out.append("❗️0% Commission")
    if _f(c, "unlimited_mileage", True) is not False:
        out.append("❗️UnlimitedMileage")
    out += ["", f"📲 {PHONE} |{TG_USER}  "]
    delivery = _f(c, "delivery", DEFAULT_DELIVERY)
    if delivery and delivery != "-":
        out.append(f"📍 FREE Pick-up & drop-off: {delivery} ")
    out += ["", "📍CARS ON MAP📍"]
    return _tidy(out)


def _tidy(lines: list[str]) -> str:
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def build(c) -> str:
    return sale_post(c) if c.deal == "sale" else rent_post(c)
