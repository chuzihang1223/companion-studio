const { normalize, textChunks } = require('./music-query.cjs');
const matches = (title, target) =>
  target.artistOnly || normalize(title) === normalize(target.title);
const selector = (e) => ({
  name: e.name,
  type: e.type,
  automation_id: e.automation_id,
  class_name: e.class_name,
});
function observedTitles(snapshot, target) {
  if (target.artistOnly) return [];
  const expected = normalize(target.title),
    rows = [];
  const boundary = (word, gap) =>
    !word ||
    !/[\p{L}\p{N}]/u.test(normalize(word.text)) ||
    (word.width > word.height * 2.2 && normalize(word.text).length === 1) ||
    gap > word.height * 0.8;
  for (const line of snapshot.lines) {
    const words = line.words || [];
    for (let start = 0; start < words.length; start++) {
      let text = '';
      for (let end = start; end < words.length; end++) {
        text += normalize(words[end].text);
        if (text.length > expected.length) break;
        if (text !== expected) continue;
        const first = words[start],
          last = words[end],
          prior = words[start - 1],
          next = words[end + 1];
        if (
          boundary(prior, prior ? first.x - prior.x - prior.width : 0) &&
          boundary(next, next ? next.x - last.x - last.width : 0)
        ) {
          const group = words.slice(start, end + 1),
            y = Math.min(...group.map((w) => w.y)),
            bottom = Math.max(...group.map((w) => w.y + w.height));
          rows.push({
            text: target.title,
            x: first.x,
            y,
            width: last.x + last.width - first.x,
            height: bottom - y,
          });
        }
      }
    }
  }
  return rows;
}
class MusicSearchResultResolver {
  static descendants(tree, key) {
    const rows = tree.elements || [],
      keys = new Set([String(key)]);
    let added = true;
    while (added) {
      added = false;
      for (const e of rows)
        if (!keys.has(String(e.key)) && keys.has(String(e.parent_key))) {
          keys.add(String(e.key));
          added = true;
        }
    }
    return rows.filter((e) => keys.has(String(e.key)));
  }
  static page(tree, snapshot) {
    const rows = tree?.elements || [],
      text = rows.map((e) => e.name || '').join('\n');
    const tabs = rows.filter(
      (e) =>
        e.enabled &&
        !e.offscreen &&
        ['Button', 'TabItem', 'Text'].includes(e.type) &&
        /^(?:已选中\s*)?(歌曲|单曲|专辑|歌单|歌手|视频|歌词|用户)$/.test(e.name || ''),
    );
    const selected = tabs.find((e) => e.selected || /^已选中/.test(e.name || '')),
      tab = selected?.name.replace(/^已选中\s*/, '');
    const visual = textChunks(snapshot || { lines: [] }),
      table =
        rows.some((e) => /歌名[/／]歌手/.test(normalize(e.name))) ||
        visual.some((e) => /歌名[/／]歌手/.test(normalize(e.text)));
    const searching =
      tabs.length >= 3 ||
      visual.filter((e) =>
        ['歌曲', '视频', '专辑', '歌单', '歌词', '歌手', '用户'].includes(normalize(e.text)),
      ).length >= 4;
    if (searching && table && (!tab || ['歌曲', '单曲'].includes(tab)))
      return { type: 'search_song', category: 'song', tabs: tabs.map(selector) };
    if (searching)
      return {
        type:
          'search_' +
          (tab === '专辑'
            ? 'album'
            : tab === '歌单'
              ? 'playlist'
              : tab === '歌手'
                ? 'artist'
                : 'other'),
        category:
          tab === '专辑'
            ? 'album'
            : tab === '歌单'
              ? 'playlist'
              : tab === '歌手'
                ? 'artist'
                : 'other',
        tabs: tabs.map(selector),
      };
    if (
      /专辑介绍|专辑简介|专辑信息/.test(
        normalize(text + '\n' + visual.map((e) => e.text).join('\n')),
      )
    )
      return { type: 'album_detail', category: table ? 'song' : 'album', tabs: [] };
    return { type: 'other', category: 'other', tabs: [] };
  }
  static fromUIA(tree, target) {
    const rows = tree.elements || [],
      byKey = new Map(rows.map((e) => [String(e.key), e])),
      page = this.page(tree),
      songs = [];
    for (const title of rows.filter(
      (e) =>
        e.enabled &&
        !e.offscreen &&
        (target.artistOnly
          ? e.type === 'Hyperlink'
          : ['Hyperlink', 'Text', 'DataItem'].includes(e.type)) &&
        matches(e.name, target) &&
        !/^歌手[:：]|^专辑[:：]/.test(e.name || ''),
    )) {
      let parent = byKey.get(String(title.parent_key));
      for (let i = 0; parent && i < 10; i++, parent = byKey.get(String(parent.parent_key))) {
        const descendants = this.descendants(tree, parent.key),
          artist = descendants.find((e) => /^歌手[:：]/.test(e.name || '')),
          album = descendants.find((e) => /^专辑[:：]/.test(e.name || '')),
          duration = descendants.find((e) => /^时长[:：]\s*\d+:\d+/.test(e.name || ''));
        if (
          !artist ||
          !album ||
          !duration ||
          descendants.filter((e) => /^歌手[:：]/.test(e.name || '')).length !== 1 ||
          descendants.filter((e) => /^专辑[:：]/.test(e.name || '')).length !== 1
        )
          continue;
        const artists = artist.name.replace(/^歌手[:：]\s*/, ''),
          albumName = album.name.replace(/^专辑[:：]\s*/, ''),
          play = descendants.find(
            (e) => e.enabled && !e.offscreen && e.type === 'Button' && e.name === '播放',
          );
        songs.push({
          kind: 'song',
          title: title.name,
          artist: artists,
          album: albumName,
          duration: duration.name.replace(/^时长[:：]/, ''),
          artist_match: !target.artist || normalize(artists).includes(normalize(target.artist)),
          enabled: true,
          source: 'uia',
          row_key: parent.key,
          title_selector: selector(title),
          play_selector: play ? selector(play) : null,
          score:
            (target.artist && normalize(artists).includes(normalize(target.artist)) ? 2000 : 0) +
            (/live|翻唱|伴奏|演唱会/i.test(title.name + ' ' + albumName) ? 0 : 100),
        });
        break;
      }
    }
    return {
      page,
      results: songs.filter((r, i) => songs.findIndex((q) => q.row_key === r.row_key) === i),
      method: 'uia_song_rows',
    };
  }
  static fromVisual(snapshot, target) {
    const chunks = textChunks(snapshot),
      header = chunks.find((e) => /歌名[/／]歌手/.test(normalize(e.text))),
      album =
        header &&
        chunks
          .filter(
            (e) =>
              normalize(e.text) === '专辑' &&
              Math.abs(e.y - header.y) < header.height * 2 &&
              e.x > header.x,
          )
          .sort((a, b) => a.x - b.x)[0],
      duration =
        header &&
        chunks.find(
          (e) => normalize(e.text) === '时长' && Math.abs(e.y - header.y) < header.height * 2,
        );
    const page = this.page(null, snapshot);
    if (!header || !album || !duration) return { page, results: [], method: 'window_table_vision' };
    const titles = [...chunks, ...observedTitles(snapshot, target)].filter(
      (e) =>
        e.y > header.y + header.height &&
        e.y < snapshot.height * 0.84 &&
        e.x >= header.x - header.height &&
        e.x + e.width < album.x &&
        matches(e.text, target),
    );
    const results = titles
      .map((t, index) => {
        const related = chunks.filter(
            (e) =>
              e.y >= t.y + t.height &&
              e.y < t.y + t.height * 2.8 &&
              e.x >= t.x - t.height &&
              e.x < album.x &&
              /[\p{L}]/u.test(e.text),
          ),
          artists = [...new Set(related.map((e) => e.text))].join(' '),
          time = chunks.find(
            (e) =>
              /\d{1,2}:\d{2}/.test(normalize(e.text)) &&
              Math.abs(e.y - t.y) < t.height * 1.5 &&
              e.x >= duration.x - duration.height,
          );
        return {
          kind: time ? 'song' : 'other',
          title: t.text.replace(/\s/g, ''),
          artist: artists,
          album:
            chunks.find(
              (e) => e.x >= album.x && e.x < duration.x && Math.abs(e.y - t.y) < t.height * 1.5,
            )?.text || '',
          duration: time?.text,
          artist_match: !target.artist || normalize(artists).includes(normalize(target.artist)),
          enabled: !!time,
          source: 'window_table_vision',
          index,
          score: /live|翻唱|伴奏|演唱会/i.test(t.text) ? 0 : 100,
        };
      })
      .filter(
        (r, i, a) =>
          a.findIndex(
            (q) => q.title === r.title && q.artist === r.artist && q.album === r.album,
          ) === i,
      );
    return { page, results, method: 'window_table_vision' };
  }
}
module.exports = { MusicSearchResultResolver };
