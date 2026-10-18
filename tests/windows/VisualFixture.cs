// MIT. Original synthetic controls only; no network, accounts, shell or user files.
using System;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Windows.Forms;

public sealed class VisualFixture : Form {
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    TextBox input; Label result, counter; Panel painted; int clicks;
    public VisualFixture() {
        Text="Companion Visual QA"; Name="VisualFixture"; Size=new Size(850,650);
        StartPosition=FormStartPosition.CenterScreen; Font=new Font("Segoe UI",12);
        input=new TextBox {Name="SampleInput",AccessibleName="Sample input",Location=new Point(25,55),Width=310};Controls.Add(input);
        var apply=new Button {Name="ApplySample",AccessibleName="Apply sample",Text="Apply sample",Location=new Point(355,50),Size=new Size(160,42)};
        result=new Label {Name="SampleResult",AccessibleName="Sample result",Text="Ready",Location=new Point(25,110),Size=new Size(600,40)};
        apply.Click+=(s,e)=>{result.Text=input.Text;result.AccessibleName=input.Text;};Controls.Add(apply);Controls.Add(result);
        counter=new Label {Name="CounterState",AccessibleName="0",Text="0",Location=new Point(690,55),Size=new Size(60,35)};
        var increment=new Button {Name="CounterAction",Text="Counter action",Location=new Point(530,50),Size=new Size(150,42)};
        increment.Click+=(s,e)=>{clicks++;counter.Text=clicks.ToString();counter.AccessibleName=counter.Text;};Controls.Add(increment);Controls.Add(counter);
        var list=new ListBox {Name="SampleList",AccessibleName="Sample list",Location=new Point(25,175),Size=new Size(230,180)};
        for(int i=0;i<100;i++)list.Items.Add("Item "+i);Controls.Add(list);
        var tree=new TreeView {Name="SampleTree",AccessibleName="Sample tree",Location=new Point(275,175),Size=new Size(230,180)};
        var root=tree.Nodes.Add("Project");root.Nodes.Add("Documents");root.Nodes.Add("Code");root.Expand();Controls.Add(tree);
        painted=new Panel {Name="PaintedArea",AccessibleName="",Location=new Point(25,390),Size=new Size(740,160),BackColor=Color.White};
        painted.Paint+=(s,e)=>{using(var f=new Font("Microsoft YaHei",20)){e.Graphics.DrawString("本地视觉测试",f,Brushes.Black,20,12);e.Graphics.DrawString("Orchard QA",f,Brushes.Black,20,60);e.Graphics.DrawString("Duplicate",f,Brushes.Black,350,12);e.Graphics.DrawString("Duplicate",f,Brushes.Black,350,70);}};Controls.Add(painted);
        var hidden=new TextBox {Name="ProtectedInput",AccessibleName="Protected field",UseSystemPasswordChar=true,Text=new string('x',8),Location=new Point(540,180),Width=180};Controls.Add(hidden);
        var rebuild=new Button {Name="RebuildControl",Text="Rebuild input",Location=new Point(535,245),Size=new Size(180,40)};
        rebuild.Click+=(s,e)=>{Controls.Remove(input);input.Dispose();input=new TextBox{Name="SampleInput",AccessibleName="Sample input",Location=new Point(25,55),Width=310};Controls.Add(input);};Controls.Add(rebuild);
        var vanish=new Button {Name="VanishControl",Text="Remove counter",Location=new Point(535,295),Size=new Size(180,40)};
        vanish.Click+=(s,e)=>{Controls.Remove(increment);increment.Dispose();};Controls.Add(vanish);
        var delay=new Button {Name="DelayedControl",Text="Show delayed",Location=new Point(25,560),Size=new Size(180,40)};
        delay.Click+=(s,e)=>{var timer=new Timer{Interval=500};timer.Tick+=(o,a)=>{timer.Stop();timer.Dispose();Controls.Add(new Label{Name="DelayedState",AccessibleName="Delayed ready",Text="Delayed ready",Location=new Point(230,565),AutoSize=true});};timer.Start();};Controls.Add(delay);
        var menu=new MenuStrip{Name="SampleMenu"};menu.Items.Add(new ToolStripMenuItem("Fixture"));MainMenuStrip=menu;Controls.Add(menu);
    }
    [STAThread] public static void Main() {
        SetProcessDpiAwarenessContext(new IntPtr(-4));Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);Application.Run(new VisualFixture());
    }
}
