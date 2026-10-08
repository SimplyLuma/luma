# SPDX-License-Identifier: Apache-2.0
"""GTK-free colour transforms corresponding to Viewer's adjustment values."""
import math


def colour_matrix(values):
    e,c,s,w,hi,sh,v,t=(values.get(k,0)/100 for k in ('exp','con','sat','warm','hi','sh','vib','tint'))
    brightness=1+e*.6+sh*.15+hi*.08
    contrast=1+c*.6-sh*.2+hi*.12
    saturation=max(0,1+s+v*.5)
    sepia=max(0,w)*.35
    angle=math.radians((w*18 if w<0 else 0)+t*14)
    co,si=math.cos(angle),math.sin(angle)
    sat=[[.213+.787*saturation,.715-.715*saturation,.072-.072*saturation],
         [.213-.213*saturation,.715+.285*saturation,.072-.072*saturation],
         [.213-.213*saturation,.715-.715*saturation,.072+.928*saturation]]
    sep=[[1-.607*sepia,.769*sepia,.189*sepia],[.349*sepia,1-.314*sepia,.168*sepia],[.272*sepia,.534*sepia,1-.869*sepia]]
    hue=[[.213+.787*co-.213*si,.715-.715*co-.715*si,.072-.072*co+.928*si],
         [.213-.213*co+.143*si,.715+.285*co+.140*si,.072-.072*co-.283*si],
         [.213-.213*co-.787*si,.715-.715*co+.715*si,.072+.928*co+.072*si]]
    def mul(a,b):return [[sum(a[i][k]*b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    matrix=mul(hue,mul(sep,sat))
    return [[x*brightness*contrast for x in row] for row in matrix], (1-contrast)*127.5


def adjust_pixels(pixels,width,height,stride,channels,values,*,cancelled=None):
    if channels not in (3,4) or stride<width*channels or len(pixels)<(height-1)*stride+width*channels:
        raise ValueError('Invalid image buffer.')
    if not any(values.get(k,0) for k in ('exp','con','hi','sh','sat','vib','warm','tint')):return bytes(pixels)
    matrix,bias=colour_matrix(values);out=bytearray(pixels)
    # Keep alpha and row padding untouched. Work against a snapshot, never the source.
    for y in range(height):
        if cancelled is not None and y % 16 == 0 and cancelled():
            return None
        for x in range(width):
            pos=y*stride+x*channels;r,g,b=pixels[pos:pos+3]
            for j,row in enumerate(matrix):out[pos+j]=max(0,min(255,round(row[0]*r+row[1]*g+row[2]*b+bias)))
    return bytes(out)


def crop_bounds(bounds,width,height):
    x,y,w,h=bounds
    if not all(math.isfinite(n) for n in bounds) or w<=0 or h<=0:raise ValueError('Invalid crop.')
    left=max(0,min(width-1,round(x)));top=max(0,min(height-1,round(y)))
    right=max(left+1,min(width,round(x+w)));bottom=max(top+1,min(height,round(y+h)))
    return left,top,right-left,bottom-top
