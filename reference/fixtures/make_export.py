#!/usr/bin/env python3
"""يبني أرشيف Facebook-export صناعي بنفس سكيما الـfixture تبعنا، بحجم واقعي:
400 محادثة · 2000 منشور · 6000 إعجاب · 3000 تعليق · صور معاد استخدامها.

الاستعمال: python3 make_export.py <out_dir> [--conv 400] [--posts 2000]
بيطلع: <out_dir>/facebook-export.zip
"""
import argparse, json, os, random, shutil, zipfile
from datetime import datetime, timedelta, timezone

ROOT = "your_activity_across_facebook"
FIRST = ["ليلى","أحمد","سمر","خالد","رهف","مالك","نور","جود","تالا","عمر","هبة","يزن",
         "رنا","سامر","ديمة","بشار","لمى","غيث","شهد","قصي","روان","أيمن","ميار","نادر"]
LAST = ["الجردي","الحلبي","الشامي","اللاذقاني","العلي","درويش","خوري","سبع","حمدان",
        "المصري","قاسم","دروبي","الرفاعي","بيطار","نعسان","عبدالله"]
WORDS = ["اليوم كان حلو كتير","الجو بشام حلو","خلصت المشروع أخيراً","المطعم جديد وعجبني",
         "صباح الخير يا شام","رجعت من السفر","قهوة الصبح","الحمدلله","تصوير من الرحلة",
         "شغل جديد بدأ","الكتاب خلص","البيت الجديد","العيلة اجتمعت","مطر اليوم بشام",
         "خطوة جديدة","تعبت بس مستمر","وقت حرير","حفلة صغيرة","طريق الشام","بحر اللاذقية"]

def ts(base, days):
    return int((base + timedelta(days=random.randint(-days, 0),
                                 minutes=random.randint(0, 1439))).timestamp())

def person():
    return f"{random.choice(FIRST)} {random.choice(LAST)}"

def build(out_dir, n_conv, n_posts, n_likes, n_comments):
    root = os.path.join(out_dir, ROOT)
    # Only ever delete the export root we generate, never the caller's dir:
    # `make_export.py /some/important/place` must not wipe it.
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    src_img = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "facebook_export", ROOT, "posts", "media", "1.jpg")

    # --- posts (منشورات + صور + روابط) ---
    posts_dir = os.path.join(root, "posts"); os.makedirs(os.path.join(posts_dir, "media"))
    shutil.copy(src_img, os.path.join(posts_dir, "media", "0.jpg"))
    posts, media_idx = [], 0
    for i in range(n_posts):
        t = ts(base, 1400)
        p = {"timestamp": t, "data": [{"post": random.choice(WORDS) + f" #{i}"}]}
        if i % 2 == 0:  # كل تاني منشور صورة
            media_idx += 1
            name = f"{media_idx}.jpg"
            shutil.copy(src_img, os.path.join(posts_dir, "media", name))
            p["attachments"] = [{"data": [{"media": {"uri": f"posts/media/{name}",
                                                     "creation_timestamp": t}}]}]
        posts.append(p)
    json.dump(posts, open(os.path.join(posts_dir,
              "your_posts__check_ins__photos_and_videos_1.json"), "w"),
              ensure_ascii=False, indent=1)

    # --- likes ---
    likes_dir = os.path.join(root, "likes_and_reactions"); os.makedirs(likes_dir)
    likes = {"reactions_v2": [
        {"timestamp": ts(base, 1400),
         "data": [{"reaction": {"reaction": random.choice(["👍","❤️","😂","😮"]),
                                "actor": person()}}]} for _ in range(n_likes)]}
    json.dump(likes, open(os.path.join(likes_dir, "likes_and_reactions_1.json"), "w"),
              ensure_ascii=False, indent=1)

    # --- comments ---
    cm_dir = os.path.join(root, "comments"); os.makedirs(cm_dir)
    comments = {"comments_v2": [
        {"timestamp": ts(base, 1400),
         "data": [{"comment": {"timestamp": ts(base, 1400), "comment": random.choice(WORDS),
                               "author": person()}}]} for _ in range(n_comments)]}
    json.dump(comments, open(os.path.join(cm_dir, "comments.json"), "w"),
              ensure_ascii=False, indent=1)

    # --- known folders the importer classifies but does not model yet ---
    # They sit at the export root, beside the activity folder, exactly as
    # Facebook lays them out.
    ads_dir = os.path.join(out_dir, "ads_information", "ads_and_topics")
    friends_dir = os.path.join(out_dir, "friends_and_followers")
    for d in (os.path.dirname(ads_dir), friends_dir):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(ads_dir)
    os.makedirs(friends_dir)
    json.dump({"topics_v2": [
        {"name": random.choice(WORDS), "value": "fixture"} for _ in range(40)]},
        open(os.path.join(ads_dir, "ads_interests.json"), "w"),
        ensure_ascii=False, indent=1)
    json.dump({"friends_v2": [{"name": person()} for _ in range(30)]},
              open(os.path.join(friends_dir, "friends.json"), "w"),
              ensure_ascii=False, indent=1)

    # --- messages ---
    inbox = os.path.join(root, "messages", "inbox"); os.makedirs(inbox)
    for c in range(n_conv):
        title = person()
        slug = f"{title.replace(' ', '_')}_{100000 + c}"
        d = os.path.join(inbox, slug); os.makedirs(os.path.join(d, "photos"))
        msgs = []
        for m in range(random.randint(20, 60)):
            tms = ts(base, 1400) * 1000
            msg = {"sender_name": title if m % 2 else "Hadi Test",
                   "timestamp_ms": tms,
                   "content": random.choice(WORDS)}
            if m % 17 == 0:
                fname = "p.jpg"; shutil.copy(src_img, os.path.join(d, "photos", fname))
                msg["photos"] = [{"uri": f"messages/inbox/{slug}/photos/{fname}",
                                  "creation_timestamp": tms // 1000}]
            msgs.append(msg)
        json.dump({"participants": [{"name": title}, {"name": "Hadi Test"}],
                   "messages": msgs, "title": title,
                   "thread_path": f"inbox/{slug}"},
                  open(os.path.join(d, "message_1.json"), "w"), ensure_ascii=False, indent=1)

    out_zip = os.path.join(out_dir, "facebook-export.zip")
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for base in (root, os.path.dirname(ads_dir), friends_dir):
            for dp, _, fs in os.walk(base):
                for f in fs:
                    full = os.path.join(dp, f)
                    z.write(full, os.path.relpath(full, out_dir).replace(os.sep, "/"))
    return out_zip, {"conversations": n_conv, "posts": n_posts, "likes": n_likes,
                     "comments": n_comments, "images": media_idx + 1}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--conv", type=int, default=400)
    ap.add_argument("--posts", type=int, default=2000)
    ap.add_argument("--likes", type=int, default=6000)
    ap.add_argument("--comments", type=int, default=3000)
    a = ap.parse_args()
    random.seed(42)
    z, stats = build(a.out_dir, a.conv, a.posts, a.likes, a.comments)
    print(json.dumps({"zip": z, "bytes": os.path.getsize(z), **stats}, ensure_ascii=False))
