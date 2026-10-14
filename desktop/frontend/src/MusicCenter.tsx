import { useEffect, useRef, useState } from 'react';
import { loadMusicLibrary } from './api';

type Props = { expanded: boolean; onClose: () => void; onOpen: () => void };
type MusicAction = { action: 'local_music_search' | 'local_music_pause'; query?: string };
const message = (cause: unknown) => (cause instanceof Error ? cause.message : String(cause));
const formatTime = (value: number) =>
  `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, '0')}`;

export default function MusicCenter({ expanded, onClose, onOpen }: Props) {
  const [tracks, setTracks] = useState<MusicTrack[]>([]);
  const [folder, setFolder] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [current, setCurrent] = useState<MusicTrack | null>(null);
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(70);
  const [error, setError] = useState('');
  const [clients, setClients] = useState<KnownApp[]>([]);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const nativeRef = useRef(false);
  const tracksRef = useRef<MusicTrack[]>([]);
  tracksRef.current = tracks;

  useEffect(() => {
    void loadMusicLibrary()
      .then(({ folder: saved, tracks: rows }) => {
        setFolder(saved);
        setTracks(rows);
      })
      .catch((cause) => setError(message(cause)));
  }, []);
  useEffect(() => {
    if (expanded)
      void window.desktopAPI
        ?.listKnownApps()
        .then((items) =>
          setClients(items.filter((item) => ['netease', 'qqmusic'].includes(item.id))),
        )
        .catch(() => {});
  }, [expanded]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!nativeRef.current) return;
      void window.desktopAPI
        ?.nativeAudioStatus()
        .then((status) => {
          setPosition(status.position);
          setDuration(status.duration);
          if (status.status === 'stopped') setPlaying(false);
        })
        .catch(() => {});
    }, 500);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<MusicAction>).detail;
      if (detail.action === 'local_music_pause') {
        void pause();
        return;
      }
      if (detail.action === 'local_music_search') {
        const term = detail.query?.toLocaleLowerCase() ?? '';
        const match = tracksRef.current.find((row) => row.title.toLocaleLowerCase().includes(term));
        if (match) void playTrack(match);
        else {
          setError(`本地曲库未找到「${detail.query}」，请先选择音乐文件夹。`);
          onOpen();
        }
      }
    };
    window.addEventListener('companion-music-action', handler);
    return () => window.removeEventListener('companion-music-action', handler);
  });
  async function playTrack(track: MusicTrack) {
    setError('');
    if (nativeRef.current) await window.desktopAPI?.stopNativeAudio().catch(() => {});
    nativeRef.current = false;
    audioRef.current?.pause();
    const base = (await window.desktopAPI?.getBackendUrl()) ?? 'http://127.0.0.1:8000';
    const url = `${base}${track.audio_url}`;
    const audio = new Audio(url);
    audioRef.current = audio;
    audio.volume = volume / 100;
    audio.preload = 'metadata';
    audio.ontimeupdate = () => setPosition(audio.currentTime);
    audio.ondurationchange = () =>
      setDuration(Number.isFinite(audio.duration) ? audio.duration : 0);
    audio.onended = () => {
      setPlaying(false);
      void next(track.id);
    };
    audio.onerror = () => {
      if (audioRef.current !== audio) return;
      if (track.format === 'wav' && window.desktopAPI) {
        void window.desktopAPI
          .playNativeAudio(url, volume / 100)
          .then((status) => {
            nativeRef.current = true;
            setDuration(status.duration);
            setPlaying(true);
            setError('');
          })
          .catch((cause) => {
            setPlaying(false);
            setError(`播放失败：${message(cause)}`);
          });
      } else {
        setPlaying(false);
        setError(`无法播放 ${track.format.toUpperCase()}，请确认系统音频设备与解码支持。`);
      }
    };
    setCurrent(track);
    setPosition(0);
    setDuration(0);
    try {
      await audio.play();
      setPlaying(true);
    } catch (cause) {
      if (!nativeRef.current && !audio.error) {
        setPlaying(false);
        setError(`播放失败：${message(cause)}`);
      }
    }
  }
  async function pause() {
    if (nativeRef.current) await window.desktopAPI?.pauseNativeAudio();
    else audioRef.current?.pause();
    setPlaying(false);
  }
  async function toggle() {
    if (!current) {
      if (tracks.length) await playTrack(tracks[0]);
      else onOpen();
      return;
    }
    if (playing) {
      await pause();
      return;
    }
    if (nativeRef.current) await window.desktopAPI?.resumeNativeAudio();
    else await audioRef.current?.play();
    setPlaying(true);
  }
  async function next(from = current?.id, delta = 1) {
    if (!tracks.length) return;
    const index = tracks.findIndex((item) => item.id === from);
    await playTrack(tracks[(index + delta + tracks.length) % tracks.length]);
  }
  async function chooseFolder() {
    try {
      const result = await window.desktopAPI?.selectMusicFolder();
      if (result) {
        setFolder(result.folder);
        setTracks(result.tracks);
        setError('');
      }
    } catch (cause) {
      setError(`读取曲库失败：${message(cause)}`);
    }
  }
  async function openClient(provider: 'netease' | 'qqmusic') {
    try {
      const result = await window.desktopAPI?.launchKnownApp(provider);
      if (!result?.installed) {
        setError(`没有检测到${provider === 'netease' ? '网易云音乐' : 'QQ 音乐'}桌面客户端。`);
        return;
      }
      if (result.status === 'cancelled') return;
      setError('');
      setClients((items) => items.map((item) => (item.id === provider ? result : item)));
    } catch (cause) {
      setError(`启动客户端失败：${message(cause)}`);
    }
  }
  const visible = tracks.filter((item) =>
    item.title.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );
  return (
    <>
      <div className="music-mini">
        <button className="music-mini-title" type="button" onClick={onOpen} title="打开本地曲库">
          ♫ <span>{current?.title ?? '本地曲库'}</span>
        </button>
        <button
          type="button"
          onClick={() => void toggle()}
          aria-label={playing ? '暂停音乐' : '播放音乐'}
        >
          {playing ? 'Ⅱ' : '▶'}
        </button>
        <button type="button" onClick={() => void next()} aria-label="下一首">
          ⏭
        </button>
      </div>
      {expanded && (
        <div className="modal-backdrop" onMouseDown={onClose}>
          <section
            className="modal music-modal"
            aria-label="本地曲库"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header>
              <h2>本地曲库</h2>
              <button
                type="button"
                className="icon-action"
                onClick={onClose}
                aria-label="关闭本地曲库"
              >
                ×
              </button>
            </header>
            <div className="music-now">
              <strong>{current?.title ?? '选择一首歌'}</strong>
              <span>{current?.format.toUpperCase() ?? '本地曲库'}</span>
            </div>
            <div className="music-progress">
              <span>{formatTime(position)}</span>
              <input
                aria-label="播放进度"
                type="range"
                min="0"
                max={Math.max(duration, 1)}
                step="1"
                value={Math.min(position, Math.max(duration, 1))}
                onChange={(event) => {
                  const value = Number(event.target.value);
                  setPosition(value);
                  if (!nativeRef.current && audioRef.current) audioRef.current.currentTime = value;
                }}
                disabled={!current || nativeRef.current}
              />
              <span>{formatTime(duration)}</span>
            </div>
            <div className="music-controls">
              <button onClick={() => void next(current?.id, -1)} aria-label="上一首">
                ⏮
              </button>
              <button onClick={() => void toggle()} aria-label={playing ? '暂停' : '播放'}>
                {playing ? 'Ⅱ' : '▶'}
              </button>
              <button onClick={() => void next()} aria-label="下一首">
                ⏭
              </button>
              <label>
                音量{' '}
                <input
                  aria-label="音乐音量"
                  type="range"
                  min="0"
                  max="100"
                  value={volume}
                  onChange={(event) => {
                    const value = Number(event.target.value);
                    setVolume(value);
                    if (audioRef.current) audioRef.current.volume = value / 100;
                  }}
                />
              </label>
            </div>
            <h3>本地音乐</h3>
            <button type="button" className="cache-clear" onClick={() => void chooseFolder()}>
              选择音乐文件夹
            </button>
            <p className="setting-note">
              {folder ?? '尚未选择文件夹'} · {tracks.length} 首
            </p>
            <input
              className="music-search"
              aria-label="搜索歌曲"
              placeholder="搜索本地歌曲"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <div className="music-track-list">
              {visible.map((item) => (
                <button
                  key={item.id}
                  className={current?.id === item.id ? 'selected' : ''}
                  onClick={() => void playTrack(item)}
                >
                  <span>{item.title}</span>
                  <small>{item.format.toUpperCase()}</small>
                </button>
              ))}
              {!visible.length && <p className="setting-note">本地曲库没有匹配的歌曲。</p>}
            </div>
            <h3>音乐客户端</h3>
            <div className="music-providers">
              {(['netease', 'qqmusic'] as const).map((id) => {
                const client = clients.find((item) => item.id === id);
                return (
                  <div className="music-client" key={id}>
                    <strong>{id === 'netease' ? '网易云音乐' : 'QQ 音乐'}</strong>
                    <small>
                      {client?.installed
                        ? client.status === 'running'
                          ? '运行中'
                          : client.status === 'launch_requested'
                            ? '已请求启动'
                            : '已安装'
                        : '未检测到客户端'}
                    </small>
                    <button onClick={() => void openClient(id)} disabled={!client?.installed}>
                      打开桌面客户端
                    </button>
                  </div>
                );
              })}
            </div>
            <p className="setting-note">
              第三方音乐由官方客户端播放；应用内播放器继续用于用户选择的本地文件。
            </p>
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
          </section>
        </div>
      )}
    </>
  );
}
