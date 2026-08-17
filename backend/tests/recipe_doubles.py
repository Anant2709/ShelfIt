"""A fake OpenAI client for non-streaming JSON recipe proposals."""

from types import SimpleNamespace


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)


class FakeCompletion:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]


class FakeCompletions:
    def __init__(self, content, error=None):
        self.content = content
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return FakeCompletion(self.content)


class FakeClient:
    def __init__(self, content=None, error=None):
        self.chat = SimpleNamespace(completions=FakeCompletions(content, error))

    @property
    def calls(self):
        return self.chat.completions.calls


def week(title="Tomato rice", ingredients=None, meals_per_day=2, extra=None, kcal=None):
    from app.services.recipes import SLOTS_FOR_COUNT

    slots = SLOTS_FOR_COUNT[meals_per_day]
    meals = []
    for day in range(7):
        for slot in slots:
            meal = {
                "day_offset": day,
                "slot": slot,
                "title": title,
                "ingredients": list(ingredients or ["Tomatoes", "Rice"]),
            }
            if kcal is not None:
                meal["kcal"] = kcal
            meals.append(meal)
    if extra:
        meals.extend(extra)
    return {"meals": meals}
