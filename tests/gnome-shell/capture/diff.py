import sys, gi, json
gi.require_version("GdkPixbuf","2.0"); from gi.repository import GdkPixbuf
a=GdkPixbuf.Pixbuf.new_from_file(sys.argv[1]); b=GdkPixbuf.Pixbuf.new_from_file(sys.argv[2])
w=min(a.get_width(),b.get_width()); h=min(a.get_height(),b.get_height())
pa=a.get_pixels(); pb=b.get_pixels(); na=a.get_n_channels(); nb=b.get_n_channels(); ra=a.get_rowstride(); rb=b.get_rowstride()
tot=n=big=0
for y in range(0,h,2):
  for x in range(0,w,2):
    d=sum(abs(pa[y*ra+x*na+c]-pb[y*rb+x*nb+c]) for c in range(3)); tot+=d; n+=1; big+= d>30
print(json.dumps({"a":[a.get_width(),a.get_height()],"b":[b.get_width(),b.get_height()],"meanAbsDiff":round(tot/n/3,3),"fractionOver30":round(big/n,5)}))
