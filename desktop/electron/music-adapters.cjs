const { normalize, delay, parseQuery, textChunks, interruption } = require('./music-query.cjs');
const { MusicAdapterBase } = require('./music-adapter-base.cjs');
function searchHint(snapshot) {
  // Short prior queries and rotating placeholders are valid; no minimum text width.
  const header = snapshot.lines.filter(
    (line) =>
      line.y < snapshot.height * 0.12 &&
      line.x > snapshot.width * 0.22 &&
      line.x < snapshot.width * 0.53 &&
      line.width > 0,
  );
  return (
    header.find((line) => /搜索|搜歌|搜你/.test(line.text)) ||
    header.sort((a, b) => a.y - b.y || b.width - a.width)[0]
  );
}
function searchEdit(tree) {
  const [x, y, w, h] = tree.window.bounds;
  return (
    tree.elements
      .filter((row) => row.enabled && !row.offscreen && ['Edit', 'ComboBox'].includes(row.type))
      .find((row) => /搜索|search/i.test(row.name + ' ' + row.automation_id)) ||
    tree.elements.find(
      (row) =>
        row.enabled &&
        !row.offscreen &&
        row.type === 'Edit' &&
        row.bounds[1] < y + h * 0.14 &&
        row.bounds[0] > x + w * 0.2 &&
        row.bounds[0] < x + w * 0.6,
    )
  );
}
const selector = (row) => ({
  name: row.name,
  automation_id: row.automation_id,
  class_name: row.class_name,
  type: row.type,
});
class NetEaseMusicAdapter extends MusicAdapterBase {
  constructor(controller, app = 'netease') {
    super(controller, app);
  }
  async locateAndType() {
    const h = this.window.handle;
    let tree = await this.controller.inspect_ui_tree(this.app, h);
    const direct = searchEdit(tree);
    if (direct) {
      try {
        await this.controller.set_text(this.app, selector(direct), this.query, h);
        this.record('A', 'success', 'UI Automation search edit');
        return 'uia_edit';
      } catch (error) {
        if (interruption(error)) throw error;
        this.record('A', 'failed', String(error.message || error));
      }
    } else
      this.record(
        'A',
        'unavailable',
        'UIA ' + tree.elements.length + ' elements, no accessible search edit',
      );
    const button = tree.elements.find(
      (row) =>
        row.enabled &&
        !row.offscreen &&
        ['Button', 'Image', 'Text'].includes(row.type) &&
        /^(搜索|search)$/i.test(row.name),
    );
    if (button) {
      try {
        await this.controller.click(this.app, selector(button), h);
        tree = await this.controller.inspect_ui_tree(this.app, h);
        const edit = searchEdit(tree);
        if (!edit) throw Error('搜索按钮后仍未暴露输入框');
        await this.controller.set_text(this.app, selector(edit), this.query, h);
        this.record('B', 'success', 'Search button followed by accessible edit');
        return 'uia_button_edit';
      } catch (error) {
        if (interruption(error)) throw error;
        this.record('B', 'failed', String(error.message || error));
      }
    } else this.record('B', 'unavailable', 'No accessible search button');
    this.record(
      'C',
      'skipped',
      'No search shortcut verified for installed client; Ctrl+F is not assumed',
    );
    let snapshot = await this.controller.screenshot_region(this.app, h, 2),
      candidate = searchHint(snapshot);
    if (!candidate && snapshot.lines.some((line) => /播放器模式/.test(normalize(line.text)))) {
      // The expanded player has no search bar. Verify the observed page closes
      // before trying to locate a search target; this is not a search shortcut.
      await this.controller.hotkey(this.app, 'ESC', h);
      await delay(400);
      snapshot = await this.controller.screenshot_region(this.app, h, 2);
      candidate = searchHint(snapshot);
      this.record(
        'page',
        candidate ? 'restored' : 'unavailable',
        'Observed expanded player; Escape then fresh search-header inspection',
      );
    }
    if (!candidate) {
      this.record('D', 'failed', 'No observed header search text');
      throw Error(
        '网易云搜索定位失败：UIA未暴露可用搜索框/按钮，未验证搜索快捷键；窗口视觉未识别顶部搜索区域。已检查 ' +
          tree.elements.length +
          ' 个UIA元素。',
      );
    }
    await this.controller.click_search_header(this.app, h);
    await this.controller.hotkey(this.app, 'CTRL+A', h);
    await this.controller.type_text(this.app, this.query, h);
    this.record(
      'D',
      'success',
      'Window OCR header bounding box; ownership/focus rechecked before click',
    );
    return 'window_ocr';
  }
  async search(query) {
    this.target = parseQuery(query);
    this.query = this.target.query;
    this.selected = null;
    this.diagnostics = [];
    this.resultScale = 1;
    if (!this.query) throw Error('歌名不能为空');
    await this.wait_ready();
    await this.focus();
    const h = this.window.handle;
    let method = await this.locateAndType();
    await delay(250);
    const submitted = await this.controller.submit_exact_search(this.app, this.query, h);
    method += ' + ' + submitted.method;
    // Suggestions may still be visible while Enter navigates. Never click a
    // partial query suggestion: it can replace an artist with a playlist query.
    await delay(600);
    // The installed client can paint late search suggestions after Enter.
    // Dismiss them without choosing a different suggested query.
    await this.controller.hotkey(this.app, 'ESC', h);
    await this.controller.hotkey(this.app, 'TAB', h);
    const end = Date.now() + 16000;
    let singleSelected = false,
      lastCount = 0,
      exactRetried = false;
    do {
      let snapshot = await this.controller.screenshot_region(this.app, h, 2);
      this.resultScale = 2;
      const tabs = textChunks(snapshot).filter(
        (line) =>
          line.y > snapshot.height * 0.14 &&
          line.y < snapshot.height * 0.25 &&
          line.x > snapshot.width * 0.2,
      );
      const labels = ['歌单', '播客', '专辑', '歌手', 'MV', '用户', '歌词'],
        loaded =
          labels.filter((label) => tabs.some((t) => normalize(t.text) === normalize(label)))
            .length >= 4;
      const exactSuggestion = textChunks(snapshot).some(
        (t) =>
          t.y > snapshot.height * 0.08 &&
          t.y < snapshot.height * 0.19 &&
          normalize(t.text).replace(/^[q0]/, '') === normalize(this.query) &&
          /^[q0]/.test(normalize(t.text)),
      );
      if (exactSuggestion && !exactRetried) {
        await this.controller.submit_exact_search(this.app, this.query, h);
        exactRetried = true;
        await delay(350);
        continue;
      }
      const playlist = tabs.find((t) => normalize(t.text) === '歌单');
      // At fractional DPI 单曲 can be misread as 年0. The observed tab strip and
      // adjacent playlist tab bound the single-song tab; no screen coordinates.
      const single =
        tabs.find((t) => normalize(t.text) === '单曲') ||
        (playlist &&
          tabs
            .filter(
              (t) =>
                t.x + t.width < playlist.x &&
                playlist.x - t.x < snapshot.width * 0.08 &&
                Math.abs(t.y - playlist.y) < playlist.height / 2,
            )
            .sort((a, b) => b.x - a.x)[0]);
      if (loaded && single && !singleSelected) {
        this.record(
          'tabs',
          'observed',
          JSON.stringify({ labels: tabs.map((r) => r.text), selected: single.text }),
        );
        try {
          await this.controller.click_song_tab(this.app, h);
          singleSelected = true;
        } catch (error) {
          if (interruption(error)) throw error;
          if (!/Single-song tab not observed/.test(error.message)) throw error;
          await this.controller.hotkey(this.app, 'ESC', h);
          await this.controller.hotkey(this.app, 'TAB', h);
        }
        await delay(350);
        continue;
      }
      let results = this.results(snapshot);
      if (loaded && !results.some((row) => row.artist_match)) {
        snapshot = await this.controller.screenshot_region(this.app, h, 2);
        results = this.results(snapshot);
        if (results.some((row) => row.artist_match)) {
          this.resultScale = 2;
          this.record(
            'D',
            'resampled',
            'Short title recognized in 2x window image; bounds mapped to original window',
          );
        }
      }
      lastCount = results.length;
      const titlePresent = snapshot.lines.some(
        (line) =>
          line.y > snapshot.height * 0.09 &&
          line.y < snapshot.height * 0.22 &&
          normalize(line.text).includes(normalize(this.target.title || this.target.artist)),
      );
      if (loaded && singleSelected && results.some((row) => row.artist_match) && titlePresent) {
        this.record(
          'results',
          'success',
          'Confirmed single-song search page; ' + results.length + ' matching rows',
        );
        return {
          query: this.query,
          title: this.target.title,
          artist: this.target.artist,
          method,
          ocr_scale: this.resultScale,
          results,
          verified: true,
          diagnostics: this.diagnostics,
        };
      }
      await delay(350);
    } while (Date.now() < end);
    this.record('results', 'failed', 'No confirmed result list; matching rows=' + lastCount);
    const snapshot = await this.controller.screenshot_region(this.app, h, 2);
    this.record(
      'tabs',
      'unconfirmed',
      JSON.stringify(
        snapshot.lines
          .filter((r) => r.y < snapshot.height * 0.25 && r.x > snapshot.width * 0.2)
          .map((r) => r.text),
      ),
    );
    throw Error(
      '网易云已通过 ' +
        method +
        ' 输入查询，但等待16秒后未确认“' +
        (this.target.title || this.target.artist) +
        '”的单曲搜索列表。',
    );
  }
  results(snapshot) {
    const title = normalize(this.target.title || this.query),
      artist = normalize(this.target.artist);
    if (this.target.artistOnly) {
      const rows = snapshot.lines.filter(
        (line) =>
          line.y > snapshot.height * 0.22 &&
          line.y < snapshot.height * 0.85 &&
          line.x > snapshot.width * 0.18,
      );
      // A title sits above its artist and starts in the same text column. Album
      // cover lettering sits farther left and uses a smaller font; badges share
      // the artist baseline.
      // Skip unreadable rows rather than treating cover artwork as a song name.
      const titles = rows
        .filter((r) => normalize(r.text).includes(artist))
        .map(
          (r) =>
            rows
              .filter(
                (t) =>
                  r.y - t.y > Math.max(t.height, r.height) * 0.7 &&
                  r.y - t.y < snapshot.height * 0.068 &&
                  t.height >= r.height * 0.85 &&
                  t.x >= r.x - snapshot.width * 0.055 &&
                  t.x - r.x < snapshot.width * 0.08 &&
                  !normalize(t.text).includes(artist) &&
                  t.text.trim().length > 1,
              )
              .sort((a, b) => b.y - a.y)[0],
        )
        .filter(Boolean);
      return titles
        .filter((t, i) => titles.findIndex((r) => normalize(r.text) === normalize(t.text)) === i)
        .map((r) => ({
          title: r.text.trim(),
          index: 0,
          artist: this.target.artist,
          artist_match: true,
          score: 2000 - (/伴奏|翻唱|live|演唱会/i.test(r.text) ? 500 : 0),
        }));
    }
    return snapshot.lines
      .filter(
        (line) =>
          line.y > snapshot.height * 0.22 &&
          line.y < snapshot.height * 0.85 &&
          line.x > snapshot.width * 0.18 &&
          normalize(line.text) === title,
      )
      .map((line, index) => {
        const related = snapshot.lines
          .filter(
            (other) =>
              other.y > line.y &&
              other.y < line.y + snapshot.height * 0.068 &&
              other.x > line.x - snapshot.width * 0.04 &&
              other.x < line.x + snapshot.width * 0.25,
          )
          .map((other) => normalize(other.text))
          .join(' ');
        return {
          title: line.text.replace(/\s/g, ''),
          index,
          artist: related,
          artist_match: !artist || related.includes(artist),
          score:
            (normalize(line.text) === title ? 500 : 0) +
            (artist && related.includes(artist) ? 2000 : 0) +
            (/原唱/.test(related) ? 300 : 0) -
            (/演唱会|live|翻唱|伴奏|纯音乐/.test(line.text + ' ' + related) ? 200 : 0),
        };
      });
  }
  async get_search_results() {
    let snapshot = await this.controller.screenshot_region(
        this.app,
        this.window.handle,
        this.resultScale,
      ),
      results = this.results(snapshot);
    if (!results.some((row) => row.artist_match) && this.resultScale === 1) {
      snapshot = await this.controller.screenshot_region(this.app, this.window.handle, 2);
      results = this.results(snapshot);
      this.resultScale = 2;
    }
    return { query: this.query, ocr_scale: this.resultScale, results };
  }
  async play_song(query, index) {
    if (query) {
      this.target = parseQuery(query);
      this.query = this.target.query;
    }
    const h = this.window.handle,
      results = (await this.get_search_results()).results.sort((a, b) => b.score - a.score);
    if (!results.length) throw Error('未找到精确匹配的歌曲，没有播放相似标题或伴奏版本');
    const selected = index === undefined ? results[0] : results.find((row) => row.index === index);
    if (!selected) throw Error('所选歌曲行已变化，请重新搜索');
    if (this.target.artist && !selected.artist_match)
      throw Error('未在结果中确认指定歌手，请选择匹配的版本');
    if (
      !this.target.artistOnly &&
      index === undefined &&
      results.length > 1 &&
      results[0].score === results[1].score
    )
      throw Error('ambiguous_song: 找到多个相同匹配的版本，需要选择');
    this.selected = selected;
    this.record('selection', 'requested', JSON.stringify(selected));
    let method = 'uia';
    try {
      await this.controller.click(this.app, { name: selected.title, type: 'Text' }, h, true);
    } catch (error) {
      if (interruption(error)) throw error;
      method = 'window_ocr';
      await this.controller.click_text(
        this.app,
        selected.title,
        h,
        true,
        selected.index,
        'results',
        this.resultScale,
        true,
      );
    }
    this.record('selection', 'clicked', method + ' ' + selected.title);
    return {
      requested: true,
      query: this.query,
      title: selected.title,
      artist: this.target.artist,
      method,
    };
  }
  async get_now_playing({ passive = false } = {}) {
    const media = await this.controller
      .media(this.app)
      .catch(() => ({ available: false, sessions: [] }));
    if (media.sessions.length)
      return {
        ...(media.sessions.find((s) => s.active) || media.sessions[0]),
        source: 'windows_media_session',
      };
    if (!this.window) {
      const rows = await this.controller.list_windows(this.app);
      this.window = rows.find((w) => w.responding && w.bounds[2] >= 240 && w.bounds[3] >= 160);
      if (!this.window) throw Error('未发现音乐播放器窗口');
    }
    let state = await this.controller.call(this.app, 'player', {
      handle: this.window.handle,
      passive,
    });
    const [title, ...artists] = state.window.title.split(' - ');
    const footer = (snapshot) =>
      snapshot.ocr.lines.filter((line) => line.y > snapshot.ocr.height * 0.87);
    if (
      !artists.length &&
      normalize(title).length <= 6 &&
      ['playing', 'paused'].includes(state.visual.Status) &&
      !footer(state).some((line) => normalize(line.text).includes(normalize(title)))
    ) {
      // Short footer titles can disappear in Windows OCR at fractional DPI.
      // Keep the original player image for icon detection; map resampled text bounds back.
      state = await this.controller.call(this.app, 'player', {
        handle: this.window.handle,
        passive,
        scale: 2,
      });
    }
    const bottom = footer(state);
    const visible = bottom.map((line) => normalize(line.text)),
      titleLine = bottom.find((line) => normalize(line.text) === normalize(title));
    const artist =
      artists.join(' - ') ||
      bottom.find((line) => titleLine && line.y > titleLine.y && line.x < state.ocr.width * 0.36)
        ?.text ||
      '';
    const times = bottom.flatMap((line) =>
      [...line.text.matchAll(/(?<!\d)(\d{1,2}):(\d{2})(?!\d)/g)].map(
        (match) => Number(match[1]) * 60 + Number(match[2]),
      ),
    );
    const duration = times.length >= 2 ? times[1] : null,
      position = duration !== null && times[0] <= duration ? times[0] : null;
    const metadataVerified = !!title && !!artist && !['网易云音乐', 'QQ音乐'].includes(title);
    return {
      source: 'window_title_and_player_icon',
      title,
      artist,
      status: state.visual.Status,
      position_seconds: position,
      duration_seconds: duration,
      thumbnail: null,
      metadata_verified: metadataVerified,
      visible_title_verified: visible.some((line) => line.includes(normalize(title))),
      visual_verified: metadataVerified && ['playing', 'paused'].includes(state.visual.Status),
      capture_method: state.ocr.capture_method,
      ocr_scale: state.ocr.scale || 1,
      window: state.window,
    };
  }
  async verify_playing(query) {
    const target = parseQuery(query || this.query),
      title = target.artistOnly ? this.selected?.title : target.title,
      end = Date.now() + 12000;
    let state;
    do {
      state = await this.get_now_playing();
      if (
        title &&
        normalize(state.title) === normalize(title) &&
        (!target.artist || normalize(state.artist).includes(normalize(target.artist))) &&
        String(state.status).toLowerCase() === 'playing' &&
        (state.source === 'windows_media_session' || state.visual_verified)
      )
        return { ...state, verified: true };
      await delay(350);
    } while (Date.now() < end);
    return { ...state, verified: false, error: '当前歌名、歌手或播放状态未通过验证' };
  }
  async control(action) {
    await this.wait_ready();
    const media = await this.controller
      .media(this.app)
      .catch(() => ({ available: false, sessions: [] }));
    if (media.sessions.length) await this.controller.media(this.app, action);
    if (!media.sessions.length) {
      if (!['play', 'pause'].includes(action)) throw Error('此客户端未提供媒体会话控制');
      await this.focus();
      const state = await this.get_now_playing();
      if (
        (action === 'play' && state.status === 'paused') ||
        (action === 'pause' && state.status === 'playing')
      )
        await this.controller.call(this.app, 'click_player', { handle: this.window.handle });
    }
    const expected = action === 'pause' ? 'paused' : 'playing',
      end = Date.now() + 3500;
    let state;
    do {
      await delay(300);
      state = await this.get_now_playing({ passive: true });
      if (String(state.status).toLowerCase() === expected) return { ...state, verified: true };
    } while (Date.now() < end);
    return { ...state, verified: false };
  }
  pause() {
    return this.control('pause');
  }
  resume() {
    return this.control('play');
  }
  next() {
    return this.control('next');
  }
  previous() {
    return this.control('previous');
  }
}
const { QQMusicAdapter } = require('./qqmusic-adapter.cjs');
module.exports = {
  NetEaseMusicAdapter,
  QQMusicAdapter,
  normalize,
  searchHint,
  parseQuery,
  searchEdit,
};
