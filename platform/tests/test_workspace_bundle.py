import pytest

from workspace.bundle import bundle_html, replace_bounded


def test_selected_local_resources_are_inlined_without_changing_script_logic():
    script = '<script>const example="<link href=\'x.css\'>"; window.ready=true;</script>'
    source = f'<html><head><link rel="stylesheet" href="./styles/guide.css"></head><body><img src="images/logo.svg">{script}</body></html>'
    files = {"guide.css": {"id": "style", "kind": "css", "text": '.hero{background:url("logo.svg");color:#008485}'},
             "logo.svg": {"id": "logo", "kind": "image", "uri": "data:image/svg+xml;base64,PHN2Zy8+"}}
    result, used = bundle_html(source, files)
    assert used == ["logo", "style"]
    assert '<style data-import-asset="style">' in result
    assert "data:image/svg+xml;base64,PHN2Zy8+" in result
    assert script in result


def test_remote_urls_integrity_and_style_end_tags_cannot_be_repurposed():
    files = {"guide.css": {"id": "style", "kind": "css", "text": '</style><script>alert(1)</script>'},
             "logo.svg": {"id": "logo", "kind": "image", "uri": "data:image/svg+xml;base64,PHN2Zy8+"}}
    external = '<img src="https://remote.invalid/logo.svg"><link rel="stylesheet" href="guide.css" integrity="sha256-x">'
    result, used = bundle_html(external, files)
    assert result == external and not used
    result, _ = bundle_html('<link rel="stylesheet" href="guide.css">', files)
    assert '</style><script>' not in result


def test_resource_expansion_is_bounded_before_constructing_large_output():
    image = {"id": "image", "kind": "image", "uri": "data:image/png;base64," + "A" * 160_000}
    with pytest.raises(ValueError, match="上限|상한"):
        bundle_html('<img src="photo.png">' * 100, {"photo.png": image})
    with pytest.raises(ValueError, match="상한"):
        bundle_html('<link rel="stylesheet" href="x.css">', {
            "photo.png": image, "x.css": {"id": "style", "kind": "css", "text": '.x{background:url("photo.png")}' * 100}})
    with pytest.raises(ValueError, match="1MB"):
        replace_bounded("asset://x " * 100, "asset://x", image["uri"])


def test_conditional_stylesheets_keep_their_applicability():
    files = {"x.css": {"id": "style", "kind": "css", "text": "body{color:red}"}}
    result, used = bundle_html('<link rel="stylesheet" href="x.css" media="not all">', files)
    assert 'media="not all"' in result and used == ["style"]
    for source in ['<link rel="stylesheet" href="x.css" disabled>',
                   '<link rel="alternate stylesheet" href="x.css" title="alternate">',
                   '<link rel="stylesheet" href="x.css" onload="activate()">']:
        result, used = bundle_html(source, files)
        assert result == source and not used
