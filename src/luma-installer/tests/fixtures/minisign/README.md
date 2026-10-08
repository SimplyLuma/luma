# minisign test fixtures

Made with minisign 0.12 from Fedora 44, so the Depot verifier is checked
against the reference tool and not only against itself:

```sh
minisign -G -W -p test.pub -s test.key
minisign -S -s test.key -m catalog-4.json -t "file:catalog-4.json test fixture"
minisign -S -l -s test.key -m catalog-4.json -t "file:catalog-4.json legacy test fixture"
```

The secret key was deleted after signing. These files authenticate nothing but
this test document; the real catalogue key is the distribution workstream's.
