# Managed natural voices

This optional component adds one local English (US) LJSpeech voice through the
existing Speech Dispatcher output-module interface. The native `sd_piper`
module retains the model between utterances and reports begin/end/cancel events;
Fedora explicitly loads its basic module and disables automatic discovery, so
the package owns one AddModule registration through the existing global
`Include "clients/*.conf"` configuration path. It never edits speechd.conf or
changes DefaultModule. After install, the existing service must reload or the
user must start a new session. There is no parallel speech service, boot replay, shell speech command, or
runtime package download. Removing the RPM removes its module, voice and UI;
user books and the existing basic voices remain untouched.

Piper is pinned to v1.4.2, commit
`d6975e21a440c0d8b6e5fb7c41027409af13d44d` (GPL-3.0-or-later, bundled MIT
JSON and Unicode implementation). Luma builds its native library against Fedora
ONNX Runtime and eSpeak, replacing upstream's external project/download paths.
Speech Dispatcher's Piper module is from commit
`58d64bc40bd64369bea8b44b333a29d2d66ebf48`, LGPL-2.1-or-later, by Michael
Hansen, Derek L Davies and Sola. Its initializer is adapted to Piper 1.4.2's public
C API. Common module helpers come from the exact existing Speech Dispatcher
0.12.1 source, commit `6781ff1709eca1c3d7f748e5361a6aa157dd5f18`.

The LJSpeech medium model is by Bryce Beattie, who explicitly dedicates his
listed models to the public domain and permits rehosting. Its LJSpeech dataset
is also public domain. The model is named LJSpeech; it is not presented as Amy.

- https://github.com/OHF-Voice/piper1-gpl/tree/v1.4.2
- https://github.com/brailcom/speechd/blob/58d64bc40bd64369bea8b44b333a29d2d66ebf48/src/modules/piper.cpp
- https://brycebeattie.com/files/tts/
- https://keithito.com/LJ-Speech-Dataset/

Qualification remains open until the normal package build, actual native SSIP
voice discovery/audio/cancel, managed installation/removal, and installed
ordinary-user application checks pass. Source preparation alone does not
register this component in Depot or prove it usable.
