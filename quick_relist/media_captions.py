"""Read existing photo captions through Hub's observed dialog, using Cancel."""
from __future__ import annotations

import time
from .capture import CaptureReadError, CaptureSafetyError
from .original_media import observed_originals


def capture_captions(driver, media, listing_id, *, identity, guard, timeout=15, accepted=lambda item: None):
    from selenium.common.exceptions import StaleElementReferenceException
    records = observed_originals(media, listing_id)
    result = []

    def visible(selector, root=None):
        return [e for e in (root or driver).find_elements('css selector', selector) if e.is_displayed()]

    def wait(probe):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            guard()
            try:
                value = probe()
            except StaleElementReferenceException:
                value = None  # Retry DOM reads after a React replacement.
            if value:
                return value
            time.sleep(.1)
        raise CaptureReadError('The image caption dialog did not settle. Inspect Hub before continuing.')

    def click_read_control(find, *, centered=False):
        for _ in range(3):
            element = wait(find)
            guard()
            try:
                if centered:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center',inline:'center',behavior:'instant'});", element)
                    wait(identity)
                    clear = driver.execute_script("""const e=arguments[0],r=e.getBoundingClientRect();
                        const hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
                        return !!hit && (hit===e || e.contains(hit));""", element)
                    if clear is not True:
                        raise CaptureReadError('A floating Hub panel covers the photo caption button. Close that panel and collect again.')
                element.click()
                return
            except StaleElementReferenceException:
                # A detached element cannot receive the click. Only the two
                # read-only controls (open caption / Cancel) use this helper.
                continue
        raise CaptureReadError('Hub kept replacing a caption control. Saved reads have been retained.')

    def card_for(record):
        images = visible('#image-' + record['id'])
        if not images:
            return None
        if (len(images) != 1 or images[0].get_attribute('data-full-src') != record['url']
                or images[0].get_attribute('pos') != str(record['position'])):
            raise CaptureSafetyError('The photo identity or order changed while reading captions.')
        cards = images[0].find_elements('xpath', './ancestor::div[@aria-roledescription="sortable"][1]')
        if len(cards) != 1:
            raise CaptureSafetyError('The photo card layout changed.')
        return cards[0]

    for record in records:
        wait(identity)
        if visible('[role="dialog"],[role="alertdialog"]'):
            raise CaptureSafetyError('Close the open dialog before collecting captions.')
        def caption_button():
            card = card_for(record)
            if card is None:
                return None
            buttons = visible('button[aria-label="Add caption"][type="button"]', card)
            if len(buttons) > 1:
                raise CaptureSafetyError('The photo caption control is ambiguous.')
            return buttons[0] if len(buttons) == 1 and buttons[0].is_enabled() else None
        click_read_control(caption_button, centered=True)
        dialogs = wait(lambda: visible('[role="dialog"]'))
        if len(dialogs) != 1:
            raise CaptureSafetyError('The caption dialog is ambiguous.')
        def read_caption():
            dialogs = visible('[role="dialog"]')
            inputs = visible('input#caption', dialogs[0]) if len(dialogs) == 1 else []
            return {'value': inputs[0].get_attribute('value')} if len(inputs) == 1 else None
        value = wait(read_caption)['value']
        if not isinstance(value, str):
            raise CaptureReadError('The existing caption could not be read.')
        guard()
        def cancel_button():
            dialogs = visible('[role="dialog"]')
            buttons = [e for e in visible('button', dialogs[0])
                       if e.get_attribute('textContent').strip().casefold() == 'cancel'] if len(dialogs) == 1 else []
            return buttons[0] if len(buttons) == 1 and buttons[0].is_enabled() else None
        click_read_control(cancel_button)
        # MUI may keep hidden dialog nodes mounted. Wait for the overlay to
        # disappear and the editor identity to return after its close animation.
        wait(lambda: not visible('[role="dialog"],[role="alertdialog"]'))
        wait(identity)
        wait(lambda: card_for(record))
        item = {'id': record['id'], 'position': record['position'],
                'caption': value, 'close_verified': True}
        result.append(item)
        accepted(item)
    return result
