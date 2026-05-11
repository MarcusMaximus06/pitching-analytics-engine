from datetime import datetime, timedelta


def get_local_date_str():
    utc_now = datetime.utcnow()
    central_now = utc_now - timedelta(hours=5)
    return central_now.strftime("%Y-%m-%d")


def clean_name(name):
    if not isinstance(name, str):
        return name

    replacements = {
        r'\xc3\xad': 'í',
        r'\xc3\xa1': 'á',
        r'\xc3\xa9': 'é',
        r'\xc3\xb1': 'ñ',
        r'\xc3\xb3': 'ó',
        r'\xc3\xba': 'ú',
        r'\xc3\x8d': 'Í',
        r'\xc3\x81': 'Á',
        r'\xc3\x89': 'É'
    }

    for bad, good in replacements.items():
        name = name.replace(bad, good)

    return name


def calculate_implied_prob(american_odds):
    if american_odds > 0:
        return 100 / (american_odds + 100)
    else:
        return abs(american_odds) / (abs(american_odds) + 100)
