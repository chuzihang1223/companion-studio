using System;
using System.Drawing;
using System.Collections.Generic;
public class PlayerVisual {
 public class State { public string Status="unknown";public int X,Y,Width,Height;public int WhiteGroups; }
 // Find the player's circular red control inside the captured app, never screen coordinates.
 public static State Inspect(string file){return Inspect(file,"netease");}
 public static State Inspect(string file,string provider){
  bool qq=provider=="qqmusic";Func<Color,bool> accent=c=>qq?(c.G>135&&c.R<125&&c.B<190):(c.R>=180&&c.G<=120&&c.B<=155);
  using(var image=new Bitmap(file)){
   int w=image.Width,h=image.Height,start=(int)(h*.75);var visited=new bool[w*h];var best=new State();int largest=0;
   for(int y=start;y<h;y++)for(int x=0;x<w;x++){
    int offset=y*w+x;if(visited[offset])continue;visited[offset]=true;var color=image.GetPixel(x,y);if(!accent(color))continue;
    var queue=new Queue<int>();queue.Enqueue(offset);int count=0,left=x,right=x,top=y,bottom=y;
    while(queue.Count>0){int p=queue.Dequeue(),px=p%w,py=p/w;count++;left=Math.Min(left,px);right=Math.Max(right,px);top=Math.Min(top,py);bottom=Math.Max(bottom,py);
     foreach(int n in new[]{p-1,p+1,p-w,p+w}){if(n<start*w||n>=w*h||visited[n])continue;int nx=n%w,ny=n/w;if(Math.Abs(nx-px)+Math.Abs(ny-py)!=1)continue;visited[n]=true;var c=image.GetPixel(nx,ny);if(accent(c))queue.Enqueue(n);}
    }
    int cw=right-left+1,ch=bottom-top+1;double fill=(double)count/(cw*ch);
    if(count>largest&&cw>h*.028&&cw<h*.12&&ch>h*.028&&ch<h*.12&&(double)cw/ch>.8&&(double)cw/ch<(qq?1.8:1.25)&&fill>.45&&fill<.93){largest=count;best.X=left;best.Y=top;best.Width=cw;best.Height=ch;}
   }
   if(largest==0)return best;
   int groups=0;bool active=false;
   for(int x=best.X+(int)(best.Width*.23);x<best.X+(int)(best.Width*.78);x++){
    int count=0;for(int y=best.Y+(int)(best.Height*.25);y<best.Y+(int)(best.Height*.78);y++){var c=image.GetPixel(x,y);if(qq?(c.R<90&&c.G<100&&c.B<100):(c.R>210&&c.G>210&&c.B>210))count++;}
    bool white=count>best.Height*.18;if(white&&!active)groups++;active=white;
   }
   best.WhiteGroups=groups;best.Status=groups==2?"playing":groups==1?"paused":"unknown";return best;
  }
 }
}
