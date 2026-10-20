$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
try {
  Add-Type -AssemblyName System.Speech
  $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
  if ($request.action -eq 'probe') {
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    try {
      $voices = @($synth.GetInstalledVoices() | Where-Object Enabled | ForEach-Object {
        @{id=$_.VoiceInfo.Name; language=$_.VoiceInfo.Culture.Name}
      })
      @{recognizers=@([System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() | ForEach-Object { $_.Culture.Name }); voices=$voices} | ConvertTo-Json -Depth 5 -Compress
    } finally { $synth.Dispose() }
    exit 0
  }
  if ($request.action -eq 'synthesize') {
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $stream = New-Object IO.MemoryStream
    try {
      if ($request.voice) { $synth.SelectVoice([string]$request.voice) }
      else { $synth.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::NotSet,[System.Speech.Synthesis.VoiceAge]::NotSet,0,[Globalization.CultureInfo]::GetCultureInfo([string]$request.language)) }
      $synth.Rate = [Math]::Max(-3,[Math]::Min(3,[int]$request.rate))
      $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono)
      $synth.SetOutputToAudioStream($stream,$format)
      $synth.Speak([string]$request.text)
      $synth.SetOutputToNull()
      @{pcm_base64=[Convert]::ToBase64String($stream.ToArray()); sample_rate=16000; engine='windows-sapi'} | ConvertTo-Json -Compress
    } finally { $synth.Dispose(); $stream.Dispose() }
    exit 0
  }
  if ($request.action -ne 'recognize') { throw 'ACTION_INVALID' }
  # Event handlers run on engine threads. C# delegates avoid PowerShell's
  # thread-affine runspaces and collect all phrases until real end-of-stream.
  Add-Type -ReferencedAssemblies System.Speech -TypeDefinition @'
using System;
using System.IO;
using System.Collections.Generic;
using System.Globalization;
using System.Speech.Recognition;
using System.Threading;
public static class StudioLocalRecognition {
  public static string[] Recognize(byte[] wav, string language) {
    var words = new List<string>();
    using (var done = new ManualResetEvent(false))
    using (var stream = new MemoryStream(wav))
    using (var engine = new SpeechRecognitionEngine(CultureInfo.GetCultureInfo(language))) {
      Exception failure = null;
      engine.LoadGrammar(new DictationGrammar());
      engine.SpeechRecognized += (s,e) => { if(e.Result != null) words.Add(e.Result.Text); };
      engine.RecognizeCompleted += (s,e) => { failure=e.Error; done.Set(); };
      engine.SetInputToWaveStream(stream);
      engine.RecognizeAsync(RecognizeMode.Multiple);
      if(!done.WaitOne(20000)) { engine.RecognizeAsyncCancel(); throw new TimeoutException(); }
      if(failure != null) throw failure;
      return words.ToArray();
    }
  }
}
'@
  $bytes = [Convert]::FromBase64String([string]$request.audio_base64)
  $phrases = [StudioLocalRecognition]::Recognize($bytes,[string]$request.language)
  @{text=($phrases -join ' '); phrase_count=$phrases.Count; kind='FINAL'; native_streaming=$false; engine='windows-sapi'} | ConvertTo-Json -Compress
} catch {
  # Fixed codes only: do not print request text, audio, registry paths or errors.
  @{error='WINDOWS_SPEECH_UNAVAILABLE'} | ConvertTo-Json -Compress
  exit 1
}
