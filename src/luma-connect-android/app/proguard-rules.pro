# Bouncy Castle ASN.1 classes are referenced reflectively by the X.509 builder.
-keep class org.bouncycastle.asn1.** { *; }
-keep class org.bouncycastle.cert.** { *; }
-dontwarn org.bouncycastle.**
