vendor/ - third-party code that ships with the app
==================================================

winrt/  (and the winrt_*-3.2.1.dist-info folders)

    Python/WinRT - the Python projection of the Windows Runtime.
    https://github.com/pywinrt/pywinrt
    Copyright (c) Microsoft Corporation. Licensed under the MIT License.

typing_extensions.py  (and typing_extensions-4.16.0.dist-info)

    https://github.com/python/typing_extensions
    Licensed under the Python Software Foundation License (PSF-2.0); the
    licence text is in typing_extensions-4.16.0.dist-info/licenses/.

    winrt requires it. Vendoring winrt without it moved the failure from
    "No module named 'winrt'" to "No module named 'typing_extensions'" and
    fixed nothing - which is why main.py --selftest now imports the media
    backend with the user site-packages switched off, to prove the closure is
    complete rather than assume it.

Why it is here rather than installed
------------------------------------
The media widget reads the system media session through this projection. It
normally lives in the user's roaming site-packages, and on at least one machine
the app repeatedly came up unable to import it from there - "No module named
'winrt'" - with that directory present, populated, and on sys.path. The same
process could not write to its own %APPDATA% folder either, while writing
happily to the app folder. The cause was never established.

Rather than keep guessing, the dependency was moved somewhere known to work:
3MB beside the code, put on sys.path ahead of anything under the user profile.
See widgets/core/media.py::_ensure_user_site.

If you would rather use an installed copy, delete this folder - the loader falls
back to the user site-packages, which is where `pip install winrt-Windows.Media.Control`
puts it.
