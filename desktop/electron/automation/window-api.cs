using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
public class CompanionWindows {
 public delegate bool Callback(IntPtr handle,IntPtr parameter);
 [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left,Top,Right,Bottom; }
 [StructLayout(LayoutKind.Sequential)] struct MouseInput { public int X,Y; public uint Data,Flags,Time; public UIntPtr Extra; }
 [StructLayout(LayoutKind.Sequential)] struct KeyInput { public ushort Key,Scan; public uint Flags,Time; public UIntPtr Extra; }
 [StructLayout(LayoutKind.Explicit)] struct Union { [FieldOffset(0)] public MouseInput Mouse; [FieldOffset(0)] public KeyInput Key; }
 [StructLayout(LayoutKind.Sequential)] struct Input { public uint Type; public Union Data; }
 [DllImport("user32.dll")] public static extern bool EnumWindows(Callback callback,IntPtr parameter);
 [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr parent,Callback callback,IntPtr parameter);
 [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr context);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr handle);
 [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr handle);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr handle,out Rect bounds);
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr handle,IntPtr deviceContext,uint flags);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr handle,uint flags);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr handle,out uint pid);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr handle,StringBuilder text,int maximum);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr handle,StringBuilder text,int maximum);
 public static string WindowClass(IntPtr handle){var value=new StringBuilder(128);GetClassName(handle,value,128);return value.ToString();}
 public static bool DesktopHostClass(string windowClass,string executable){
  if(System.Text.RegularExpressions.Regex.IsMatch(windowClass??"",@"^(?:Progman|WorkerW|Shell_TrayWnd|Shell_SecondaryTrayWnd)$"))return true;
  // Explorer also owns blank ApplicationFrameWindow and desktop composition hosts.
  // Its file windows are CabinetWClass / ExploreWClass; other app owners keep their classes.
  return String.Equals(System.IO.Path.GetFileName(executable),"explorer.exe",StringComparison.OrdinalIgnoreCase)
   && !System.Text.RegularExpressions.Regex.IsMatch(windowClass??"",@"^(?:CabinetWClass|ExploreWClass)$");
 }
 static bool DesktopHost(IntPtr handle,uint pid){return DesktopHostClass(WindowClass(handle),ProcessPath((int)pid));}
 public static bool IsApplicationFrameHost(string windowClass,string executable){
  var systemHost=System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"ApplicationFrameHost.exe");
  return String.Equals(windowClass,"ApplicationFrameWindow",StringComparison.Ordinal)
   && String.Equals(executable,systemHost,StringComparison.OrdinalIgnoreCase);
 }
 static bool FrameHostsApplication(IntPtr handle,uint owner,HashSet<int> allowed){
  if(!IsApplicationFrameHost(WindowClass(handle),ProcessPath((int)owner)))return false;
  // An OS host may contain several apps. Authorize only a frame containing a
  // CoreWindow owned by a process already matched to the requested application.
  bool matched=false;
  EnumChildWindows(handle,(child,parameter)=>{
   uint pid;GetWindowThreadProcessId(child,out pid);
   if(allowed.Contains((int)pid)&&WindowClass(child)=="Windows.UI.Core.CoreWindow"){matched=true;return false;}
   return true;
  },IntPtr.Zero);
  return matched;
 }
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr handle);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr handle,int command);
 [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr handle);
 [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr handle,IntPtr after,int x,int y,int width,int height,uint flags);
 [DllImport("user32.dll")] static extern IntPtr SetActiveWindow(IntPtr handle);
 [DllImport("user32.dll",EntryPoint="GetWindowLongW")] static extern int GetWindowLong(IntPtr handle,int index);
 [StructLayout(LayoutKind.Sequential)] struct LastInput { public uint Size,Time; }
 [DllImport("user32.dll")] static extern bool GetLastInputInfo(ref LastInput input);
 [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint source,uint target,bool attach);
 [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
 [DllImport("user32.dll")] static extern uint SendInput(uint count,Input[] inputs,int size);
 [DllImport("user32.dll")] static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] static extern void mouse_event(uint flags,uint x,uint y,uint data,UIntPtr extra);
 [DllImport("user32.dll")] static extern IntPtr SendMessageTimeout(IntPtr h,uint message,IntPtr w,IntPtr l,uint flags,uint timeout,out IntPtr result);
 public static List<long> Windows(string executable){var found=new List<long>();EnumWindows((h,p)=>{uint pid;GetWindowThreadProcessId(h,out pid);if(!IsWindowVisible(h))return true;try{if(String.Equals(Process.GetProcessById((int)pid).MainModule.FileName,executable,StringComparison.OrdinalIgnoreCase))found.Add(h.ToInt64());}catch{}return true;},IntPtr.Zero);return found;}
 [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenProcess(uint access,bool inherit,uint pid);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool QueryFullProcessImageName(IntPtr process,uint flags,StringBuilder path,ref uint size);
 [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr process);
 [DllImport("kernel32.dll")] static extern bool ProcessIdToSessionId(uint pid,out uint session);
 [DllImport("kernel32.dll")] static extern bool GetProcessTimes(IntPtr process,out long created,out long exited,out long kernel,out long user);
 public static double ProcessCreated(int pid){var handle=OpenProcess(0x1000,false,(uint)pid);if(handle==IntPtr.Zero)return 0;try{long created,exited,kernel,user;return GetProcessTimes(handle,out created,out exited,out kernel,out user)?(created-116444736000000000L)/10000000.0:0;}finally{CloseHandle(handle);}}
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] static extern int GetApplicationUserModelId(IntPtr process,ref uint length,StringBuilder id);
 public static string ProcessPath(int pid){var handle=OpenProcess(0x1000,false,(uint)pid);if(handle==IntPtr.Zero)return "";try{var value=new StringBuilder(32768);uint length=32768;return QueryFullProcessImageName(handle,0,value,ref length)?value.ToString():"";}finally{CloseHandle(handle);}}
 public static string ProcessAppId(int pid){var handle=OpenProcess(0x1000,false,(uint)pid);if(handle==IntPtr.Zero)return "";try{uint length=512;var value=new StringBuilder(512);return GetApplicationUserModelId(handle,ref length,value)==0?value.ToString():"";}finally{CloseHandle(handle);}}
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] struct ProcessEntry {public uint Size,Usage,Id;public UIntPtr Heap;public uint Module,Threads,Parent;public int Priority;public uint Flags;[MarshalAs(UnmanagedType.ByValTStr,SizeConst=260)]public string Exe;}
 [DllImport("kernel32.dll")]static extern IntPtr CreateToolhelp32Snapshot(uint flags,uint pid);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]static extern bool Process32First(IntPtr snapshot,ref ProcessEntry entry);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]static extern bool Process32Next(IntPtr snapshot,ref ProcessEntry entry);
 static void AddDescendants(List<int> ids,int[] seeds,double[] started){
  var entries=new List<ProcessEntry>();var snapshot=CreateToolhelp32Snapshot(2,0);if(snapshot==new IntPtr(-1))return;
  try{var entry=new ProcessEntry{Size=(uint)Marshal.SizeOf(typeof(ProcessEntry))};if(Process32First(snapshot,ref entry))do{entries.Add(entry);}while(Process32Next(snapshot,ref entry));}finally{CloseHandle(snapshot);}
  var allowed=new HashSet<int>(ids);var times=new Dictionary<int,double>();bool changed;
  foreach(var id in ids)times[id]=ProcessCreated(id);
  for(int i=0;i<(seeds??new int[0]).Length;i++){var at=started!=null&&i<started.Length?started[i]:0;var actual=ProcessCreated(seeds[i]);if(at<=0||(actual>0&&Math.Abs(actual-at)>1))continue;allowed.Add(seeds[i]);times[seeds[i]]=at;}
  uint ownSession;ProcessIdToSessionId((uint)Process.GetCurrentProcess().Id,out ownSession);
  do{changed=false;foreach(var entry in entries){if(!allowed.Contains((int)entry.Parent)||allowed.Contains((int)entry.Id))continue;
   if(System.Text.RegularExpressions.Regex.IsMatch(entry.Exe,@"^(?:explorer|cmd|powershell|pwsh|svchost|rundll32|dllhost|wscript|cscript|conhost)\.exe$",System.Text.RegularExpressions.RegexOptions.IgnoreCase))continue;
   uint session;if(!ProcessIdToSessionId(entry.Id,out session)||session!=ownSession)continue;
   var created=ProcessCreated((int)entry.Id);double parentTime;times.TryGetValue((int)entry.Parent,out parentTime);
   if(created<=0||(parentTime>0&&created<parentTime))continue;
   allowed.Add((int)entry.Id);times[(int)entry.Id]=created;ids.Add((int)entry.Id);changed=true;
  }}while(changed);
 }
 public static List<int> MatchingProcesses(string[] executables,string location,string appId,bool descendants=false,int[] seeds=null,double[] started=null){
  var ids=new List<int>();string root="";try{if(!String.IsNullOrWhiteSpace(location)){var full=System.IO.Path.GetFullPath(location).TrimEnd('\\');if(full.Length>3&&!System.Text.RegularExpressions.Regex.IsMatch(full,@"\\(?:Windows|System32|Program Files(?: \(x86\))?)$",System.Text.RegularExpressions.RegexOptions.IgnoreCase))root=full+"\\";}}catch{}
  foreach(var process in Process.GetProcesses()){try{var value=ProcessPath(process.Id);bool matches=false;foreach(var executable in executables??new string[0]){try{if(!String.IsNullOrEmpty(value)&&String.Equals(System.IO.Path.GetFullPath(value),System.IO.Path.GetFullPath(executable),StringComparison.OrdinalIgnoreCase))matches=true;}catch{}}
   if(!matches&&!String.IsNullOrEmpty(root)&&!String.IsNullOrEmpty(value))matches=value.StartsWith(root,StringComparison.OrdinalIgnoreCase);
   if(!matches&&!String.IsNullOrEmpty(appId))matches=String.Equals(ProcessAppId(process.Id),appId,StringComparison.OrdinalIgnoreCase);
   if(matches)ids.Add(process.Id);
  }catch{}finally{process.Dispose();}}if(descendants)AddDescendants(ids,seeds,started);return ids;
 }
 public static List<long> WindowsForProcesses(int[] ids,bool includeHidden=false){var found=new List<long>();var allowed=new HashSet<int>(ids??new int[0]);if(allowed.Count==0)return found;EnumWindows((h,p)=>{uint pid;GetWindowThreadProcessId(h,out pid);if((!allowed.Contains((int)pid)&&!FrameHostsApplication(h,pid,allowed))||DesktopHost(h,pid))return true;if(IsWindowVisible(h))found.Add(h.ToInt64());else if(includeHidden){Rect bounds;GetWindowRect(h,out bounds);var title=Title(h);if(bounds.Right-bounds.Left>=240&&bounds.Bottom-bounds.Top>=160&&!String.IsNullOrWhiteSpace(title)&&!System.Text.RegularExpressions.Regex.IsMatch(title,@"^_|(?:_wnd_|message|GDI\+|ClassName|Default IME|cross_mgs)",System.Text.RegularExpressions.RegexOptions.IgnoreCase))found.Add(h.ToInt64());}return true;},IntPtr.Zero);return found;}
 [DllImport("user32.dll")] static extern bool PostMessage(IntPtr handle,uint message,IntPtr w,IntPtr l);
 public static bool CloseWindow(IntPtr handle){return PostMessage(handle,0x10,IntPtr.Zero,IntPtr.Zero);}
 public static string Title(IntPtr h){var value=new StringBuilder(512);GetWindowText(h,value,512);return value.ToString();}
 public static bool Responding(IntPtr h){IntPtr result;return SendMessageTimeout(h,0,IntPtr.Zero,IntPtr.Zero,2,500,out result)!=IntPtr.Zero;}
 public static bool Locked(){uint pid;GetWindowThreadProcessId(GetForegroundWindow(),out pid);try{var name=Process.GetProcessById((int)pid).ProcessName;return name.Equals("LockApp",StringComparison.OrdinalIgnoreCase)||name.Equals("LogonUI",StringComparison.OrdinalIgnoreCase);}catch{return false;}}
 static uint InputTime(){var input=new LastInput{Size=(uint)Marshal.SizeOf(typeof(LastInput))};GetLastInputInfo(ref input);return input.Time;}
 public static string LastFocusFailure {get;private set;}
 static bool FocusInterrupted(uint inputTime){
  if(Locked()){LastFocusFailure="desktop_locked";return true;}
  if(InputTime()!=inputTime){LastFocusFailure="user_interruption";return true;}
  return false;
 }
 public static bool Focus(IntPtr h){
  LastFocusFailure="";
  uint inputTime=InputTime();bool wasTopmost=(GetWindowLong(h,-20)&8)!=0;
  for(int attempt=0;attempt<3;attempt++){
   if(FocusInterrupted(inputTime))return false;
   uint pid;var foreground=GetForegroundWindow();var previous=GetWindowThreadProcessId(foreground,out pid);var target=GetWindowThreadProcessId(h,out pid);var current=GetCurrentThreadId();
   bool a=previous!=0&&previous!=current&&AttachThreadInput(current,previous,true);bool b=target!=0&&target!=current&&target!=previous&&AttachThreadInput(current,target,true);
   try{
    ShowWindow(h,9);BringWindowToTop(h);
    // Temporary target-only Z order recovery; restore its original topmost state below.
    if(attempt>0&&!wasTopmost)SetWindowPos(h,new IntPtr(-1),0,0,0,0,3);
    SetForegroundWindow(h);SetActiveWindow(h);
    for(int poll=0;poll<5;poll++){if(GetAncestor(GetForegroundWindow(),2)==h)return true;if(FocusInterrupted(inputTime))return false;System.Threading.Thread.Sleep(40);}
   }finally{if(attempt>0&&!wasTopmost)SetWindowPos(h,new IntPtr(-2),0,0,0,0,3);if(b)AttachThreadInput(current,target,false);if(a)AttachThreadInput(current,previous,false);}
  }
  LastFocusFailure="focus_unavailable";return false;
 }
 public static void RequireFocus(IntPtr h){if(Locked())throw new InvalidOperationException("desktop_locked: Windows desktop is locked");if(GetAncestor(GetForegroundWindow(),2)!=h)throw new InvalidOperationException("user_interruption: target window lost focus");}
 static void Key(ushort key,bool up,ushort scan=0,bool unicode=false){var input=new Input{Type=1,Data=new Union{Key=new KeyInput{Key=key,Scan=scan,Flags=(up?2u:0u)|(unicode?4u:0u)}}};if(SendInput(1,new[]{input},Marshal.SizeOf(typeof(Input)))!=1)throw new InvalidOperationException("Windows rejected input");}
 public static void Type(IntPtr h,string value){RequireFocus(h);foreach(char c in value){RequireFocus(h);Key(0,false,c,true);Key(0,true,c,true);}}
 public static void Press(IntPtr h,string combination){RequireFocus(h);ushort key;bool ctrl=combination.StartsWith("CTRL+");var name=ctrl?combination.Substring(5):combination;switch(name){case "ENTER":key=13;break;case "ESC":key=27;break;case "SPACE":key=32;break;case "TAB":key=9;break;case "A":key=65;break;case "F":key=70;break;case "L":key=76;break;default:throw new InvalidOperationException("Unsupported key");}if(ctrl)Key(17,false);Key(key,false);Key(key,true);if(ctrl)Key(17,true);}
 public static void Click(IntPtr h,int x,int y,bool twice){RequireFocus(h);Rect r;GetWindowRect(h,out r);if(x<r.Left||x>=r.Right||y<r.Top||y>=r.Bottom)throw new InvalidOperationException("Click outside authorized window");SetCursorPos(x,y);for(int i=0;i<(twice?2:1);i++){RequireFocus(h);mouse_event(2,0,0,0,UIntPtr.Zero);mouse_event(4,0,0,0,UIntPtr.Zero);if(twice)System.Threading.Thread.Sleep(90);}}
 public static void Hover(IntPtr h,int x,int y){RequireFocus(h);Rect r;GetWindowRect(h,out r);if(x<r.Left||x>=r.Right||y<r.Top||y>=r.Bottom)throw new InvalidOperationException("Hover outside authorized window");SetCursorPos(x,y);}
}
