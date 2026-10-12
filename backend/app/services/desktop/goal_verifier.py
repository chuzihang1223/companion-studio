"""Goal-level checks are stricter than whether a process was launched."""
class GoalVerifier:
    def verify(self, goal, details):
        if not details or any(item['status']!='done' for item in details): return False
        if '播放' in goal and any(word in goal for word in ('音乐','歌曲','歌','网易云','QQ')):
            return any(item['name'] in {'music.play','media.play'} and item['result'].get('verified') is True
                       for item in details)
        if any(item['name']=='coding.task' for item in details):
            return all(item['result'].get('verified') is True for item in details if item['name']=='coding.task')
        return True
