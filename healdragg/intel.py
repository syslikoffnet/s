"""Определение пола и страны по имени — без отправки данных наружу.

Основной режим офлайновый: словари имён 12 стран (кириллица и латиница),
определение письменности и аккуратные эвристики по окончаниям. Если в
``NAME_INTEL_ONLINE`` включить сеть, результат уточняется через genderize.io
и nationalize.io — но только по имени и с коротким таймаутом.
"""

from __future__ import annotations

import json
import logging
import os
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("healdragg.intel")

COUNTRIES: dict[str, str] = {
    "RU": "Россия",
    "CN": "Китай",
    "JP": "Япония",
    "KR": "Корея",
    "AR": "Арабские страны",
    "IN": "Индия",
    "ID": "Индонезия",
    "TR": "Турция",
    "UZ": "Узбекистан",
    "KZ": "Казахстан",
    "KG": "Киргизия",
    "TJ": "Таджикистан",
}

#: Имена по странам. Одна и та же строка может встречаться в нескольких
#: странах — тогда страна считается неуверенной, а пол остаётся надёжным.
NAMES: dict[str, dict[str, tuple[str, ...]]] = {
    "RU": {
        "male": (
            "александр", "сергей", "дмитрий", "андрей", "алексей", "максим", "иван",
            "артём", "артем", "евгений", "михаил", "роман", "никита", "павел",
            "владимир", "игорь", "олег", "денис", "кирилл", "константин", "тимур",
            "руслан", "марат", "вадим", "виктор", "григорий", "степан", "фёдор",
            "федор", "юрий", "ярослав", "пётр", "петр", "семён", "семен",
        ),
        "female": (
            "анна", "мария", "елена", "ольга", "наталья", "ирина", "татьяна",
            "светлана", "юлия", "екатерина", "дарья", "анастасия", "виктория",
            "полина", "ксения", "алёна", "алена", "валентина", "галина", "любовь",
            "надежда", "вера", "софья", "софия", "милана", "алина", "диана",
            "камила", "евгения", "марина", "настя", "лена", "оля", "катя",
        ),
    },
    "UZ": {
        "male": (
            "азамат", "бекзод", "жасур", "шохрух", "санжар", "улугбек", "фаррух",
            "дилшод", "ислом", "рустам", "темур", "тимур", "бобур", "жахонгир",
            "сардор", "дониёр", "дониер", "комил", "нозим", "отил", "шерзод",
            "элёр", "элер", "акмал", "акмаль", "бек", "узбек",
        ),
        "female": (
            "дильноза", "дилноза", "нилуфар", "мадина", "севара", "гулноза",
            "заррина", "шахзода", "малика", "ситора", "фотима", "наргиза",
            "гульнара", "гульнора", "лола", "зухра", "муниса", "шохзода",
            "дильшода", "дилшода", "бону", "робия", "сабина",
        ),
    },
    "KZ": {
        "male": (
            "аян", "алихан", "данияр", "ерасыл", "нурсултан", "тимур", "арман",
            "мирас", "алдияр", "санжар", "бекзат", "ерик", "жандос", "кайрат",
            "нурлан", "тамерлан", "азамат", "диас", "алишер", "даулет", "даулет",
            "мадияр", "султан", "олжас", "ержан", "асхат", "бауыржан",
        ),
        "female": (
            "айгерим", "аружан", "дильназ", "дилназ", "жансая", "мадина",
            "камила", "айша", "сабина", "томирис", "айзере", "алия", "дана",
            "жазира", "индира", "карина", "лаура", "меруерт", "назерке",
            "сауле", "айнур", "гульмира", "гульназ", "динара", "жанна",
        ),
    },
    "KG": {
        "male": (
            "азамат", "бакыт", "улан", "нурбек", "эрмек", "айбек", "данияр",
            "нурлан", "тилек", "алмаз", "эмир", "бектур", "кубаныч", "талант",
            "элдияр", "нурсултан", "мирбек", "адилет", "адил", "акылбек",
        ),
        "female": (
            "айнура", "гуля", "жамиля", "назира", "аида", "асель", "айпери",
            "гулнара", "жылдыз", "каныкей", "нурзада", "салтанат", "таалайгул",
            "бегайым", "айсулуу", "кымбат", "даткайым", "жамал", "нургуль",
        ),
    },
    "TJ": {
        "male": (
            "фирдавс", "рустам", "джамшид", "фарход", "шохрух", "мехроб",
            "сухроб", "джахонгир", "бободжон", "икром", "лоик", "нурали",
            "парвиз", "саид", "умед", "хуршед", "шерзод", "эмомали", "юсуф",
        ),
        "female": (
            "мадина", "нигина", "фаришта", "гулнора", "заррина", "мехрангез",
            "сабрина", "шахноза", "дилноза", "фируза", "хуршеда", "зулайхо",
            "робия", "ситора", "таманно", "фарзона", "манижа", "гулдаста",
        ),
    },
    "TR": {
        "male": (
            "mehmet", "mustafa", "ahmet", "ali", "hüseyin", "huseyin", "hasan",
            "ibrahim", "murat", "emre", "burak", "yusuf", "ismail", "osman",
            "serkan", "kemal", "onur", "tolga", "barış", "baris", "can", "cem",
            "deniz", "eren", "furkan", "kerem", "mert", "ozan", "selim", "volkan",
        ),
        "female": (
            "ayşe", "ayse", "fatma", "emine", "hatice", "zeynep", "elif", "merve",
            "esra", "selin", "büşra", "busra", "damla", "deniz", "ece", "eda",
            "gamze", "gizem", "irem", "melis", "nilüfer", "nilufer", "özge",
            "ozge", "pinar", "seda", "sevgi", "şeyma", "seyma", "tuğçe", "tugce",
        ),
    },
    "IN": {
        "male": (
            "raj", "kumar", "amit", "rahul", "rohit", "sunil", "manoj", "arjun",
            "vijay", "sanjay", "rajesh", "suresh", "ravi", "anil", "deepak",
            "vikram", "aditya", "arav", "karan", "nikhil", "prakash", "shiva",
            "sameer", "imran", "farhan", "abdul", "rakesh", "ashok", "harish",
        ),
        "female": (
            "priya", "anita", "pooja", "neha", "kavita", "sneha", "deepika",
            "ananya", "aditi", "aishwarya", "anjali", "divya", "isha", "jyoti",
            "kiran", "lakshmi", "meera", "nisha", "payal", "rekha", "ria",
            "sanya", "shreya", "sonia", "sunita", "swati", "tanya", "vidya",
        ),
    },
    "ID": {
        "male": (
            "budi", "agus", "joko", "rudi", "andi", "dedi", "eko", "arif",
            "fajar", "putra", "bayu", "dimas", "galih", "hendra", "ilham",
            "jaya", "krisna", "lukman", "maman", "nanda", "opan", "rizky",
            "slamet", "taufik", "wahyu", "yudi", "zaki", "bambang", "doni",
        ),
        "female": (
            "sari", "dewi", "ayu", "putri", "rina", "indah", "lina", "fitri",
            "nur", "ani", "bella", "citra", "diah", "eka", "fitria", "gita",
            "hanna", "intan", "jihan", "kartika", "lestari", "maya", "nadia",
            "oktavia", "pertiwi", "ratna", "sinta", "tiara", "vina", "wulan",
        ),
    },
    "AR": {
        "male": (
            "mohammed", "muhammad", "ahmed", "ahmad", "ali", "omar", "khalid",
            "yusuf", "yousef", "hussain", "hussein", "saeed", "said", "abdullah",
            "abdallah", "mustafa", "ibrahim", "ismail", "hamza", "karim", "tariq",
            "faisal", "salem", "sultan", "majid", "rashid", "naser", "zaid",
            "mohamed", "ammar", "anas", "bilal",
        ),
        "female": (
            "fatima", "fatma", "aisha", "ayesha", "maryam", "mariam", "noura",
            "nora", "layla", "leila", "sara", "sarah", "huda", "amina", "aminah",
            "rania", "zainab", "zeinab", "lina", "salma", "yasmin", "yasmeen",
            "dana", "reem", "rim", "shaima", "ghada", "heba", "hala", "amal",
            "warda", "nadia",
        ),
    },
    "CN": {
        "male": (
            "伟", "强", "磊", "军", "洋", "勇", "杰", "涛", "明", "超", "刚",
            "平", "辉", "鹏", "华", "飞", "鑫", "波", "斌", "宇", "浩", "凯",
            "健", "俊", "帆", "旭", "宁", "龙", "林", "峰",
        ),
        "female": (
            "芳", "娜", "敏", "静", "丽", "娟", "艳", "霞", "玲", "燕", "婷",
            "雪", "倩", "颖", "洁", "梅", "琳", "晶", "欣", "悦", "琳", "露",
            "薇", "萍", "红", "丹", "青", "云", "月", "兰",
        ),
    },
    "JP": {
        "male": (
            "はると", "ひろし", "たけし", "そうた", "ゆうと", "あきら", "りく",
            "はる", "つよし", "けんた", "しょう", "だいき", "なおと", "まさと",
            "ゆうき", "かずき", "たくみ", "りょう", "こうへい", "しん",
        ),
        "female": (
            "さくら", "ゆい", "あおい", "ひなた", "めい", "みお", "はな", "りん",
            "かえで", "つむぎ", "ゆき", "あや", "まい", "ほのか", "みゆ", "えま",
            "さな", "のぞみ", "あすか", "ちはる", "ゆな", "かんな", "もも",
        ),
    },
    "KR": {
        "male": (
            "민준", "서준", "도윤", "예준", "시우", "하준", "지호", "지후",
            "준우", "현우", "건우", "우진", "선우", "정민", "성민", "동현",
            "태윤", "승현", "재원", "민재",
        ),
        "female": (
            "서연", "지우", "하은", "민서", "지유", "윤서", "채원", "수아",
            "지아", "예린", "서아", "하윤", "지민", "수빈", "다은", "유나",
            "소율", "예은", "가은", "나연",
        ),
    },
}

#: Исключения: мужские имена, которые по окончанию похожи на женские.
MALE_EXCEPTIONS = {
    "никита", "илья", "кузьма", "фома", "савва", "данила", "гаврила",
    "лука", "миша", "саша", "женя", "валя", "шура", "дима", "паша",
}

_CYR = set("абвгдеёжзийклмнопрстуфхцчшщъыьэюя")


@dataclass(frozen=True, slots=True)
class NameGuess:
    name: str
    gender: str
    gender_confidence: float
    country: str | None
    country_name: str | None
    country_confidence: float
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "gender": self.gender,
            "gender_confidence": round(self.gender_confidence, 2),
            "country": self.country,
            "country_name": self.country_name,
            "country_confidence": round(self.country_confidence, 2),
            "source": self.source,
        }


_INDEX: dict[str, list[tuple[str, str]]] = {}


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", (value or "").strip().lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.replace("ё", "е")


def _build_index() -> dict[str, list[tuple[str, str]]]:
    if _INDEX:
        return _INDEX
    for country, by_gender in NAMES.items():
        for gender, names in by_gender.items():
            for name in names:
                key = _normalize(name)
                if not key:
                    continue
                _INDEX.setdefault(key, []).append((country, gender))
    return _INDEX


def detect_script(value: str) -> str:
    for ch in value or "":
        code = ord(ch)
        if 0x4E00 <= code <= 0x9FFF:
            return "han"
        if 0x3040 <= code <= 0x30FF:
            return "kana"
        if 0xAC00 <= code <= 0xD7AF:
            return "hangul"
        if 0x0600 <= code <= 0x06FF:
            return "arabic"
        if ch.lower() in _CYR:
            return "cyrillic"
    return "latin"


def _gender_by_ending(name: str, script: str) -> tuple[str, float]:
    if not name:
        return "unknown", 0.0
    if name in MALE_EXCEPTIONS:
        return "male", 0.66
    if script == "cyrillic":
        if name.endswith(("а", "я")):
            return "female", 0.62
        return "male", 0.6
    if script == "latin":
        if name.endswith(("a", "ia", "ella", "ina", "ita", "ie")):
            return "female", 0.58
        return "male", 0.56
    return "unknown", 0.0


def guess(name: str, *, online: bool | None = None) -> NameGuess:
    """Определяет пол и страну по имени. Сеть — только если разрешена."""
    raw = (name or "").strip()
    if not raw:
        return NameGuess("", "unknown", 0.0, None, None, 0.0, "empty")
    script = detect_script(raw)
    normalized = _normalize(raw)

    forced = {
        "han": "CN",
        "hangul": "KR",
        "kana": "JP",
        "arabic": "AR",
    }.get(script)

    matches = _build_index().get(normalized, [])
    if forced is not None:
        gender_matches = [g for (_, g) in matches]
        gender = gender_matches[0] if gender_matches else _gender_by_ending(normalized, script)[0]
        confidence = 0.9 if gender != "unknown" else 0.5
        return NameGuess(
            raw,
            gender,
            confidence,
            forced,
            COUNTRIES[forced],
            0.9,
            "script",
        )

    if matches:
        countries = {country for country, _ in matches}
        genders = {gender for _, gender in matches}
        gender = next(iter(genders)) if len(genders) == 1 else _gender_by_ending(normalized, script)[0]
        preferred = ("RU", "UZ", "KZ", "KG", "TJ") if script == "cyrillic" else (
            "TR", "ID", "IN", "AR", "RU",
        )
        country = next((c for c in preferred if c in countries), sorted(countries)[0])
        country_confidence = 0.78 if len(countries) == 1 else 0.6
        gender_confidence = 0.85 if len(genders) == 1 else 0.62
        return NameGuess(
            raw,
            gender,
            gender_confidence,
            country,
            COUNTRIES.get(country),
            country_confidence,
            "dictionary",
        )

    gender, confidence = _gender_by_ending(normalized, script)
    if online is None:
        online = os.environ.get("NAME_INTEL_ONLINE", "").strip().lower() in {"1", "true", "yes"}
    if online:
        refined = _online(raw)
        if refined is not None:
            return refined
    return NameGuess(raw, gender, confidence, None, None, 0.0, "heuristic")


def _online(name: str) -> NameGuess | None:
    """Необязательное уточнение через genderize.io / nationalize.io."""
    gender = "unknown"
    gender_confidence = 0.0
    country: str | None = None
    try:
        query = urllib.parse.urlencode({"name": name})
        with urllib.request.urlopen(
            f"https://api.genderize.io/?{query}", timeout=2.5
        ) as response:  # noqa: S310 — фиксированный https-хост
            data = json.loads(response.read().decode("utf-8", "replace"))
            if data.get("gender"):
                gender = str(data["gender"])
                gender_confidence = float(data.get("probability") or 0.0)
    except Exception as exc:  # noqa: BLE001
        log.debug("genderize недоступен: %s", exc)
    try:
        query = urllib.parse.urlencode({"name": name})
        with urllib.request.urlopen(
            f"https://api.nationalize.io/?{query}", timeout=2.5
        ) as response:  # noqa: S310
            data = json.loads(response.read().decode("utf-8", "replace"))
            countries = data.get("country") or []
            if countries:
                top = max(countries, key=lambda item: float(item.get("probability") or 0))
                code = str(top.get("country_id") or "")
                if code:
                    country = code
    except Exception as exc:  # noqa: BLE001
        log.debug("nationalize недоступен: %s", exc)
    if gender == "unknown" and country is None:
        return None
    return NameGuess(
        name,
        gender,
        gender_confidence,
        country,
        COUNTRIES.get(country or "", country),
        0.7 if country else 0.0,
        "online",
    )


def lookup_names(values: list[str]) -> list[dict[str, Any]]:
    return [guess(value).as_dict() for value in values if (value or "").strip()]


__all__ = ["COUNTRIES", "NAMES", "NameGuess", "detect_script", "guess", "lookup_names"]
