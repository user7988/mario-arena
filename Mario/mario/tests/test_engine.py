import unittest, sys, pathlib
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import game_engine as g
class PhysicsTests(unittest.TestCase):
    def test_jump_and_land(self):
        w=g.create_world(['one']); p=w['players']['one']; y=p['y']
        g.step(w,{'one':{'jump':True}},.02,lambda *a:None)
        self.assertLess(p['y'],y)
        for _ in range(100):g.step(w,{},.02,lambda *a:None)
        self.assertTrue(p['grounded']);self.assertAlmostEqual(p['y'],y)
    def test_move_bounded(self):
        w=g.create_world(['one']);p=w['players']['one']
        for _ in range(300):g.step(w,{'one':{'left':True}},.05,lambda *a:None)
        self.assertGreaterEqual(p['x'],0)
    def test_collect_coin(self):
        w=g.create_world(['one']);p=w['players']['one'];c=w['coins'][0]
        p['x']=c['x']-10;p['y']=c['y']-15
        earned=[]
        g.step(w,{},.01,lambda *a:earned.append(a))
        self.assertEqual(earned,[('one',10)])
        g.step(w,{},.01,lambda *a:earned.append(a))
        self.assertEqual(len(earned),1)
    def test_snapshot(self):
        w=g.create_world(['one','two']);s=g.snapshot(w)
        self.assertEqual(len(s['players']),2)
        self.assertEqual(s['width'],g.WIDTH)
if __name__=='__main__':unittest.main()
