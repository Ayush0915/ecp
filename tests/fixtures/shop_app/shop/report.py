"""Small reports over order data."""


def find_duplicates(items: list) -> list:
    unused = 0
    dups = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if items[i] == items[j] and items[i] not in dups:
                dups.append(items[i])
    return dups
