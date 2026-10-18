// MIT. Synthetic Windows GUI; no network, accounts, shell, or user files.
using System;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;
[assembly: System.Runtime.Versioning.TargetFramework(".NETFramework,Version=v4.8")]

public sealed class VisualFixture : Window {
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    Canvas canvas=new Canvas(); TextBox input; TextBlock result,counter,generation; int clicks,rebuilds;
    T Put<T>(T element,string id,double x,double y,double w,double h) where T:FrameworkElement {
        element.Name=id;AutomationProperties.SetAutomationId(element,id);
        Canvas.SetLeft(element,x);Canvas.SetTop(element,y);element.Width=w;element.Height=h;
        canvas.Children.Add(element);return element;
    }
    Button Button(string id,string text,double x,double y,Action click) {
        var button=Put(new Button{Content=text},id,x,y,155,34);
        button.Click+=(s,e)=>click();return button;
    }
    public VisualFixture(){
        Title="Companion Visual QA";Width=850;Height=690;WindowStartupLocation=WindowStartupLocation.CenterScreen;
        FontFamily=new FontFamily("Segoe UI");FontSize=15;Background=Brushes.White;Content=canvas;
        input=Put(new TextBox(),"SampleInput",25,45,310,32);
        result=Put(new TextBlock{Text="Ready"},"SampleResult",25,100,500,30);
        Button("ApplySample","Apply sample",355,45,()=>result.Text=input.Text);
        counter=Put(new TextBlock{Text="0"},"CounterState",705,50,60,30);
        var increment=Button("CounterAction","Counter action",530,45,()=>counter.Text=(++clicks).ToString());
        var list=Put(new ListBox(),"SampleList",25,155,230,175);for(int i=0;i<100;i++)list.Items.Add("Item "+i);
        var tree=Put(new TreeView(),"SampleTree",275,155,230,175);
        var root=new TreeViewItem{Header="Project",IsExpanded=true};root.Items.Add(new TreeViewItem{Header="Documents"});root.Items.Add(new TreeViewItem{Header="Code"});tree.Items.Add(root);
        Put(new PasswordBox{Password=new string('x',8)},"ProtectedInput",540,155,180,32);
        generation=Put(new TextBlock{Text="0"},"RebuildGeneration",730,215,45,30);
        Button("RebuildControl","Rebuild input",535,215,()=>{canvas.Children.Remove(input);input=Put(new TextBox(),"SampleInput",35,45,310,32);generation.Text=(++rebuilds).ToString();});
        Button("VanishControl","Remove counter",535,265,()=>canvas.Children.Remove(increment));
        var painted=Put(new PaintedArea(),"PaintedArea",25,370,740,150);
        Button("SensitiveToggle","Sensitive paint",535,315,()=>{painted.Sensitive=!painted.Sensitive;painted.InvalidateVisual();});
        Button("DelayedControl","Show delayed",25,550,()=>{var timer=new DispatcherTimer{Interval=TimeSpan.FromMilliseconds(500)};timer.Tick+=(s,e)=>{timer.Stop();Put(new TextBlock{Text="Delayed ready"},"DelayedState",220,555,230,30);};timer.Start();});
        var menu=Put(new Menu(),"SampleMenu",0,0,800,30);menu.Items.Add(new MenuItem{Header="Fixture"});
    }
    [STAThread] public static void Main(){System.Windows.Media.RenderOptions.ProcessRenderMode=System.Windows.Interop.RenderMode.SoftwareOnly;SetProcessDpiAwarenessContext(new IntPtr(-4));new Application().Run(new VisualFixture());}
}
public sealed class PaintedArea:FrameworkElement {
    public bool Sensitive;
    protected override void OnRender(DrawingContext drawing){
        drawing.DrawRectangle(Brushes.White,null,new Rect(0,0,ActualWidth,ActualHeight));
        if(Sensitive){Draw(drawing,"API Key: synthetic-value-only",20,10);return;}
        Draw(drawing,"本地视觉测试",20,10);Draw(drawing,"Orchard QA",20,65);
        Draw(drawing,"Duplicate",360,10);Draw(drawing,"Duplicate",360,65);
    }
    void Draw(DrawingContext drawing,string text,double x,double y){drawing.DrawText(new FormattedText(text,CultureInfo.GetCultureInfo("zh-CN"),FlowDirection.LeftToRight,new Typeface("Microsoft YaHei"),24,Brushes.Black,VisualTreeHelper.GetDpi(this).PixelsPerDip),new Point(x,y));}
}
