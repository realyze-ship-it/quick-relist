"""Explicit local wording reviews for Hub's shorter brochure field."""
from .form_copy import CopyStopped
from .relist_plan import media_plan


def source_brochure(collection):
    page = next(p for p in collection['source']['pages'] if p['page_key'] == 'media')
    return media_plan(page)['brochure_description']


def latest_review(drafts, job_id, collection):
    for saved in drafts.list_captures(job_id):
        value = saved['capture']
        if (value.get('schema_version') == 'hub-brochure-review/v1'
                and value.get('source_capture_id') == collection['source_capture_id']):
            if value.get('original') != source_brochure(collection):
                raise CopyStopped('The brochure review no longer matches its original.')
            return {'id':saved['id'], **value}
    return None


def save_review(drafts, job_id, collection, text):
    if not isinstance(text, str) or not text.strip() or len(text) > 250:
        raise CopyStopped('Review a nonempty brochure summary of at most 250 characters.')
    return drafts.save_capture(job_id, {'schema_version':'hub-brochure-review/v1', 'complete':False,
        'source_capture_id':collection['source_capture_id'], 'original':source_brochure(collection),
        'reviewed':text})


def apply_review(plan, drafts, job_id, collection):
    text = plan.get('media', {}).get('brochure_description', '')
    if len(text) <= 250:
        return
    review = latest_review(drafts, job_id, collection)
    if not review:
        raise CopyStopped('Hub limits brochure summaries to 250 characters. Review a shorter summary before relisting.')
    if not isinstance(review.get('reviewed'), str) or not 0 < len(review['reviewed']) <= 250:
        raise CopyStopped('The reviewed brochure summary is invalid.')
    plan['media']['brochure_description'] = review['reviewed']
    plan['brochure_review_id'] = review['id']
