// Original Companion Studio window-scoped capture and geometry. No desktop blit.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;

public static class VisualWindow {
    [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct Point { public int X, Y; }
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out Rect r);
    [DllImport("user32.dll")] static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
    [DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr h);
    [DllImport("user32.dll")] static extern IntPtr GetWindowDpiAwarenessContext(IntPtr h);
    [DllImport("user32.dll")] static extern int GetAwarenessFromDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] static extern bool GetWindowDisplayAffinity(IntPtr h, out uint affinity);
    [DllImport("user32.dll")] static extern IntPtr WindowFromPoint(Point point);
    [DllImport("user32.dll")] static extern IntPtr GetAncestor(IntPtr h, uint flags);
    [DllImport("user32.dll")] static extern IntPtr MonitorFromWindow(IntPtr h, uint flags);
    [DllImport("shcore.dll")] static extern int GetDpiForMonitor(IntPtr monitor, int kind, out uint x, out uint y);
    [DllImport("user32.dll")] static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] static extern void mouse_event(uint flags, uint x, uint y, uint data, UIntPtr extra);

    public static Dictionary<string, object> Geometry(long handle) {
        IntPtr h = new IntPtr(handle); Rect r;
        if (!GetWindowRect(h, out r)) throw new InvalidOperationException("WINDOW_IDENTITY_CHANGED");
        uint dx, dy;
        if (GetDpiForMonitor(MonitorFromWindow(h, 2), 0, out dx, out dy) != 0) throw new InvalidOperationException("DPI_MAPPING_INVALID");
        var monitors = new List<object>(); var signature = new List<string>();
        foreach (Screen s in Screen.AllScreens) {
            var b = s.Bounds;
            monitors.Add(new Dictionary<string, object> { {"id", s.DeviceName}, {"bounds", new int[]{b.X,b.Y,b.Width,b.Height}}, {"primary",s.Primary} });
            signature.Add(s.DeviceName+":"+b.ToString());
        }
        return new Dictionary<string, object> {
            {"bounds",new int[]{r.Left,r.Top,r.Right-r.Left,r.Bottom-r.Top}},
            {"window_dpi",GetDpiForWindow(h)}, {"monitor_dpi",dx},
            {"dpi_awareness",GetAwarenessFromDpiAwarenessContext(GetWindowDpiAwarenessContext(h))},
            {"monitors",monitors.ToArray()}, {"layout_signature",String.Join(";",signature.ToArray())},
            {"coordinate_space","physical_screen_px"}
        };
    }

    public static bool Hit(long handle, double x, double y) {
        if (Double.IsNaN(x) || Double.IsNaN(y) || Double.IsInfinity(x) || Double.IsInfinity(y)) return false;
        return GetAncestor(WindowFromPoint(new Point {X=(int)x,Y=(int)y}),2) == new IntPtr(handle);
    }

    public static void Wheel(long handle, double x, double y, string direction) {
        if (!Hit(handle,x,y)) throw new InvalidOperationException("ELEMENT_OCCLUDED");
        SetCursorPos((int)x,(int)y);
        int amount = (direction == "up" || direction == "right") ? 120 : -120;
        mouse_event(direction == "left" || direction == "right" ? 0x1000u : 0x800u,0,0,unchecked((uint)amount),UIntPtr.Zero);
    }

    public static Dictionary<string, object> Capture(long handle, int[] crop, int[][] masks, string scope, string display) {
        IntPtr h=new IntPtr(handle); Rect r; uint affinity;
        // Display affinity can be unavailable on non-layered windows. A declared
        // protected surface is always refused; PrintWindow failure never falls
        // back to copying the screen (which could include unrelated windows).
        if (GetWindowDisplayAffinity(h,out affinity) && affinity != 0) throw new InvalidOperationException("SCREEN_CAPTURE_PROTECTED");
        if (!GetWindowRect(h,out r)) throw new InvalidOperationException("SCREEN_CAPTURE_FAILED");
        int width=r.Right-r.Left, height=r.Bottom-r.Top;
        if(width<32 || height<32 || width>4096 || height>4096) throw new InvalidOperationException("SCREEN_CAPTURE_LIMIT");
        Rectangle area=new Rectangle(0,0,width,height); int ox=r.Left, oy=r.Top;
        if(crop!=null) {
            if(crop.Length!=4 || crop[0]<0 || crop[1]<0 || crop[2]<=0 || crop[3]<=0 || (long)crop[0]+crop[2]>width || (long)crop[1]+crop[3]>height) throw new InvalidOperationException("DPI_MAPPING_INVALID");
            area=new Rectangle(crop[0],crop[1],crop[2],crop[3]); ox+=crop[0];oy+=crop[1];
        }
        using(var full=new Bitmap(width,height,PixelFormat.Format32bppArgb)) {
            using(var g=Graphics.FromImage(full)) {
                g.Clear(Color.Black);IntPtr dc=g.GetHdc();bool okay;
                try {okay=PrintWindow(h,dc,2);} finally {g.ReleaseHdc(dc);}
                if(!okay)throw new InvalidOperationException("SCREEN_CAPTURE_FAILED");
                foreach(int[] m in masks) {
                    if(m.Length!=4)continue;
                    var box=Rectangle.Intersect(new Rectangle(m[0]-r.Left,m[1]-r.Top,m[2],m[3]),new Rectangle(0,0,width,height));
                    if(box.Width>0 && box.Height>0)g.FillRectangle(Brushes.Black,box);
                }
            }
            Rectangle canvasArea=area;
            if(scope=="display") {
                Screen chosen=null;
                foreach(Screen s in Screen.AllScreens)if(s.DeviceName==display)chosen=s;
                if(chosen==null)throw new InvalidOperationException("DISPLAY_CHANGED");
                if(!chosen.Bounds.IntersectsWith(new Rectangle(r.Left,r.Top,width,height)))throw new InvalidOperationException("DPI_MAPPING_INVALID");
                canvasArea=new Rectangle(0,0,chosen.Bounds.Width,chosen.Bounds.Height);ox=chosen.Bounds.X;oy=chosen.Bounds.Y;
                if(canvasArea.Width>4096 || canvasArea.Height>4096)throw new InvalidOperationException("SCREEN_CAPTURE_LIMIT");
            }
            double scale=Math.Min(1.0,2048.0/Math.Max(canvasArea.Width,canvasArea.Height));
            using(var output=new Bitmap(Math.Max(1,(int)(canvasArea.Width*scale)),Math.Max(1,(int)(canvasArea.Height*scale)))) {
                using(var g=Graphics.FromImage(output)) {
                    g.Clear(Color.Black);g.ScaleTransform((float)scale,(float)scale);
                    if(scope=="display")g.DrawImageUnscaled(full,r.Left-ox,r.Top-oy);
                    else g.DrawImage(full,new Rectangle(0,0,area.Width,area.Height),area,GraphicsUnit.Pixel);
                }
                using(var bytes=new MemoryStream()) {
                    output.Save(bytes,ImageFormat.Png);
                    if(bytes.Length>2097152)throw new InvalidOperationException("SCREEN_CAPTURE_LIMIT");
                    return new Dictionary<string,object>{{"png",bytes.ToArray()},{"width",output.Width},{"height",output.Height},{"origin",new int[]{ox,oy}},{"scale",scale},{"scope",scope},{"redacted_regions",masks.Length}};
                }
            }
        }
    }
}
