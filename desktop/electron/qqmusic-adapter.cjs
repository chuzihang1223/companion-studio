const { MusicAdapterBase } = require('./music-adapter-base.cjs');
const { MusicSearchResultResolver: Resolver } = require('./music-search-result-resolver.cjs');
const { normalize, parseQuery, delay, interruption } = require('./music-query.cjs');
const selector = (e) => ({
  name: e.name,
  type: e.type,
  automation_id: e.automation_id,
  class_name: e.class_name,
});
class QQMusicAdapter extends MusicAdapterBase {
  constructor(controller) {
    super(controller, 'qqmusic');
  }
  async observeResults() {
    const tree = await this.controller.inspect_ui_tree(this.app, this.window.handle),
      uia = Resolver.fromUIA(tree, this.target);
    if (uia.results.length) return { ...uia, tree };
    const snapshot = await this.controller.screenshot_region(this.app, this.window.handle, 2),
      visual = Resolver.fromVisual(snapshot, this.target);
    if (process.env.COMPANION_TEST_DIAGNOSTICS === '1')
      require('node:fs').writeFileSync(
        require('node:path').join(this.controller.cache, 'qqmusic-observation.json'),
        JSON.stringify({ tree, snapshot, visual }),
      );
    return {
      ...visual,
      page: uia.page.type === 'search_song' ? uia.page : visual.page,
      tree,
      snapshot,
    };
  }
  async search(query) {
    this.target = parseQuery(query);
    this.query = this.target.query;
    this.selected = null;
    this.diagnostics = [];
    if (!this.query) throw this.error('search_failed', 'Empty QQ music query');
    if (this.context)
      Object.assign(this.context, {
        targetSong: this.target.title,
        targetArtist: this.target.artist,
      });
    await this.wait_ready();
    await this.focus();
    const h = this.window.handle;
    const tree = await this.controller.inspect_ui_tree(this.app, h),
      [x, y, w, height] = tree.window.bounds;
    const edit = tree.elements.find(
      (e) =>
        e.enabled &&
        !e.offscreen &&
        e.type === 'Edit' &&
        e.bounds[1] < y + height * 0.15 &&
        e.bounds[0] > x + w * 0.2,
    );
    let method;
    if (edit) {
      await this.controller.set_text(this.app, selector(edit), this.query, h);
      method = 'uia_edit';
    } else {
      await this.controller.click_search_header(this.app, h);
      await this.controller.hotkey(this.app, 'CTRL+A', h);
      await this.controller.type_text(this.app, this.query, h);
      method = 'window_header_vision';
    }
    await this.controller.hotkey(this.app, 'ENTER', h);
    this.record('qq.search', 'submitted', method + ' exact query');
    // QQ may open delayed suggestions after Enter while the edit retains focus.
    // Escape dismisses that overlay before observing the category and song rows.
    await delay(350);
    await this.controller.hotkey(this.app, 'ESC', h);
    const end = Date.now() + 16000;
    let categorySelected = false,
      last;
    do {
      last = await this.observeResults();
      const page = last.page;
      if (!categorySelected && page.type.startsWith('search_')) {
        await this.controller.call(this.app, 'qq_song_tab', { handle: h });
        categorySelected = true;
        this.record('qq.category', 'selected', '歌曲; fresh UIA or exact visual tab');
        continue;
      }
      const rows = last.results.filter((r) => r.kind === 'song' && r.enabled && r.artist_match);
      if (categorySelected && page.type === 'search_song' && rows.length) {
        this.record(
          'qq.results',
          'classified',
          JSON.stringify(
            rows.map(({ kind, title, artist, album, source }) => ({
              kind,
              title,
              artist,
              album,
              source,
            })),
          ),
        );
        return this.result({
          verified: true,
          query: this.query,
          results: rows,
          page,
          method: last.method,
          diagnostics: this.diagnostics,
        });
      }
      const text =
        last.tree.elements.map((e) => e.name || '').join(' ') +
        (last.snapshot?.lines.map((e) => e.text).join(' ') || '');
      if (/找到\s*0\s*首|没有找到相关|未找到相关|暂无搜索结果/.test(text))
        throw this.error('song_not_found', 'QQ search explicitly reported no songs');
      await delay(250);
    } while (Date.now() < end);
    this.record(
      'qq.results',
      'failed',
      JSON.stringify({
        page: last?.page,
        method: last?.method,
        count: last?.results.length,
        uia_count: last?.tree.elements.length,
      }),
    );
    throw this.error(
      last?.page.type === 'search_song' ? 'song_not_found' : 'search_failed',
      'QQ song category / playable exact row was not confirmed',
    );
  }
  async get_search_results() {
    const resolved = await this.observeResults();
    return this.result({
      query: this.query,
      page: resolved.page,
      results: resolved.results,
      method: resolved.method,
    });
  }
  async play_song(query, index) {
    if (query) {
      this.target = parseQuery(query);
      this.query = this.target.query;
    }
    for (let attempt = 0; attempt < 2; attempt++) {
      const observed = await this.observeResults(),
        rows = observed.results
          .filter((r) => r.kind === 'song' && r.enabled && r.artist_match)
          .sort((a, b) => b.score - a.score);
      if (!rows.length) throw this.error('song_not_found', 'No classified playable QQ song row');
      if (
        !this.target.artistOnly &&
        index === undefined &&
        rows.length > 1 &&
        rows[0].score === rows[1].score
      )
        throw this.error('ambiguous_song', 'QQ exact song has indistinguishable versions');
      const selected = rows[index ?? 0];
      if (!selected) throw this.error('song_not_found', 'Requested QQ song row changed');
      this.selected = selected;
      this.record(
        'qq.selection',
        'requested',
        JSON.stringify({
          attempt,
          title: selected.title,
          artist: selected.artist,
          album: selected.album,
          kind: selected.kind,
          source: selected.source,
        }),
      );
      const clicked = await this.controller.call(this.app, 'qq_play_song', {
        handle: this.window.handle,
        title: selected.title,
        artist: selected.source === 'uia' ? selected.artist : this.target.artist,
        album: selected.source === 'uia' ? selected.album : '',
        allow_album: observed.page.type === 'album_detail',
      });
      this.record('qq.selection', 'clicked', clicked.method);
      const after = await this.observeResults();
      if (after.page.type === 'album_detail') {
        this.record(
          'qq.recovery',
          'observed',
          'Album details after click; inspect tracks before returning to search',
        );
        if (after.results.some((r) => r.kind === 'song' && r.artist_match)) {
          if (attempt === 0) continue;
        } else if (attempt === 0) {
          const back = after.tree.elements.find(
            (e) => e.type === 'Button' && e.enabled && !e.offscreen && e.name === '后退',
          );
          if (back) await this.controller.click(this.app, selector(back), this.window.handle);
          await this.search(this.query);
          continue;
        }
        throw this.error(
          'selection_failed',
          'QQ album recovery exhausted; no repeated coordinates',
        );
      }
      return this.result({
        requested: true,
        verified: true,
        title: selected.title,
        artist: selected.artist,
        album: selected.album,
        method: clicked.method,
        result_type: 'song',
      });
    }
    throw this.error('selection_failed', 'QQ song selection recovery exhausted');
  }
  async get_now_playing({ passive = true } = {}) {
    const media = await this.controller.media(this.app).catch(() => ({ sessions: [] }));
    if (media.sessions?.length)
      return this.result({
        ...(media.sessions.find((e) => e.active) || media.sessions[0]),
        source: 'windows_media_session',
      });
    this.window ??= await this.wait_ready();
    const state = await this.controller.call(this.app, 'player', {
        handle: this.window.handle,
        passive,
        scale: 2,
      }),
      [title, ...artists] = state.window.title.trim().split(' - '),
      artist = artists.join(' - ').trim();
    const footer = state.ocr.lines
        .filter((e) => e.y > state.ocr.height * 0.83)
        .sort((a, b) => a.x - b.x),
      times = footer.flatMap((e) =>
        [...normalize(e.text).matchAll(/(?<!\d)(\d{1,2}):(\d{2})(?!\d)/g)].map(
          (m) => Number(m[1]) * 60 + Number(m[2]),
        ),
      );
    const elements = state.elements || [],
      [x, y, w, h] = state.window.bounds,
      controls = elements.filter(
        (e) =>
          e.enabled &&
          !e.offscreen &&
          e.bounds[1] > y + h * 0.82 &&
          e.bounds[0] > x + w * 0.4 &&
          e.bounds[0] < x + w * 0.75,
      );
    const pause = controls.some((e) => e.type === 'Button' && e.name === '暂停'),
      play = controls.some((e) => e.type === 'Button' && e.name === '播放');
    const clocks = elements
      .filter(
        (e) => e.type === 'Text' && e.bounds[1] > y + h * 0.82 && /^\d{1,2}:\d{2}$/.test(e.name),
      )
      .sort((a, b) => a.bounds[0] - b.bounds[0])
      .map((e) => e.name.split(':').reduce((a, b) => a * 60 + Number(b), 0));
    const values = clocks.length >= 2 ? clocks : times,
      position = values.length >= 2 && values[0] <= values[1] ? values[0] : null,
      duration = values.length >= 2 ? values[1] : null;
    return this.result({
      source: 'qq_window_player',
      title,
      artist,
      status: pause ? 'playing' : play ? 'paused' : state.visual.Status,
      position_seconds: position,
      duration_seconds: duration,
      window: state.window,
      visual_verified:
        !!title &&
        !!artist &&
        (pause || play || ['playing', 'paused'].includes(state.visual.Status)),
    });
  }
  async verify_playing(query) {
    const target = parseQuery(query || this.query),
      wanted = target.artistOnly ? this.selected?.title : target.title,
      end = Date.now() + 14000;
    let previous, state;
    do {
      state = await this.get_now_playing();
      const match =
        normalize(state.title) === normalize(wanted) &&
        (!target.artist || normalize(state.artist).includes(normalize(target.artist))) &&
        state.status === 'playing';
      if (match && state.source === 'windows_media_session')
        return this.result({ ...state, verified: true, proof: 'qq_media_session' });
      if (match && state.visual_verified && state.position_seconds !== null) {
        if (previous && state.position_seconds > previous.position_seconds)
          return this.result({
            ...state,
            verified: true,
            proof: 'qq_title_player_and_progress',
            progress_change: state.position_seconds - previous.position_seconds,
          });
        previous = state;
      }
      const tree = await this.controller.inspect_ui_tree(this.app, this.window.handle),
        text = tree.elements.map((e) => e.name || '').join(' ');
      if (
        /暂无音源|当前地区无法|歌曲暂时无法播放|开通.*(?:播放|试听)|购买.*(?:播放|试听)/.test(text)
      )
        throw this.error(
          'resource_unavailable',
          'QQ account/resource restriction observed after playback request',
        );
      await delay(450);
    } while (Date.now() < end);
    return this.result({
      ...state,
      verified: false,
      error: 'QQ title / playing icon / progress did not agree',
      error_code: 'verification_failed',
      public_error: this.publicError('verification_failed'),
    });
  }
  async control(action) {
    await this.wait_ready();
    await this.focus();
    const state = await this.get_now_playing();
    if (
      (action === 'pause' && state.status === 'playing') ||
      (action === 'play' && state.status === 'paused')
    )
      await this.controller.call(this.app, 'click_player', { handle: this.window.handle });
    if (!['play', 'pause'].includes(action))
      return this.result({
        verified: false,
        error: 'QQ operation is not available through the observed player',
      });
    const after = await this.get_now_playing();
    return this.result({
      ...after,
      verified: after.status === (action === 'pause' ? 'paused' : 'playing'),
    });
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
module.exports = { QQMusicAdapter };
