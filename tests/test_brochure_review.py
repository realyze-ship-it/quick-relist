from copy import deepcopy
import pytest
from quick_relist import brochure_review as module
from quick_relist.drafts import DraftStore
from quick_relist.form_copy import CopyStopped


def test_shorter_wording_requires_an_explicit_immutable_review(monkeypatch):
    original='Synthetic brochure. ' * 15
    monkeypatch.setattr(module,'source_brochure',lambda collection:original)
    drafts=DraftStore(':memory:')
    collection={'source_capture_id':'source'}
    plan={'media':{'brochure_description':original,'description_text':'Full original description'}}
    before=deepcopy(plan)
    with pytest.raises(CopyStopped,match='Review a shorter summary'):
        module.apply_review(plan,drafts,'job',collection)
    assert plan==before
    review=module.save_review(drafts,'job',collection,'Reviewed short summary')
    module.apply_review(plan,drafts,'job',collection)
    assert plan['media']=={'brochure_description':'Reviewed short summary','description_text':'Full original description'}
    assert plan['brochure_review_id']==review
    assert drafts.get_capture(review)['capture']['original']==original
    with pytest.raises(CopyStopped):module.save_review(drafts,'job',collection,'x'*251)
    with pytest.raises(CopyStopped):module.apply_review(deepcopy(before),drafts,'job',{'source_capture_id':'different'})
