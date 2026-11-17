"""Batch DOM reads using the same visibility test as Selenium WebElement."""
from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=4)
def with_displayed(script):
    atom = files('selenium.webdriver.remote').joinpath('isDisplayed.js').read_text(encoding='utf-8')
    return 'const displayed = (' + atom + ');' + script
