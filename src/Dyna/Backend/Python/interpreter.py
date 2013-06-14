#!/usr/bin/env python

"""
TODO
====

 - TODO: `--draw` should be a post-processor

 - TODO: call pre/post-processors from repl.

 - AggregatorConflict should now always be raised by Dyna compiler.

 - vbench: a script which tracks performace over time (= git commits).

 - profiler workflow

   kcachegrind
     $ (PYTHONPATH=src/Dyna/Backend/Python/ pycachegrind src/Dyna/Backend/Python/interpreter.py examples/papa.dyna)

   cProfile + snakeviz
     $ python -m cProfile -o prof src/Dyna/Backend/Python/interpreter.py examples/force.dyna >/dev/null && snakeviz prof


 - unit tests and code coverage.

 - doc tests for Dyna code.

 - TODO: think about indices as memoized queries

 - let Dyna do some of the work for you. think about using Dyna to maintain
   rules and update handlers.

 - magic templates transform for backward chaining, for example:

   :- sigmoid(X) := needs(X), 1 / (1 + exp(-X)).
   :- needs(0.5).


BIGGER (new features)
=====================

 - TODO: operator for getattr (this will generate slightly different code
   because we don't want to look up by string -- i.e. call the getattr builtin).

 - hook for python imports?

   :- python: from numpy import exp, sqrt, log


FASTER
======

 - TODO: specialize calls to emit, don't build the big dictionaries if the
   aggregator doesn't use them. Consider generate both version (or an argument
   to the update handler which will skip the appropriate code paths).

 - TODO: faster charts (dynamic argument types? jason's trie data structure)

 - TODO: teach planner to prefer not to use the value column, because it's not
   indexed.

 - TODO: dynac should provide routines for building terms. We can hack something
   together with anf output, but this will be prety kludgy and inefficient.

 - TODO: prioritization

 - TODO: BAggregators aren't very efficient.

 - interning with integers instead of deduplicated instances of Term.


STRONGER (robustness)
=====================

 - TODO: error handling, some stuff isn't a proper transaction yet.

 - Context manager for disabling ^C in certain blocks.

 - TODO: catch compiler errors (for example, ^C while compiling results in a
   "Compiler panic!  This is almost assuredly not your fault!...").


USERS
=====

 - TODO: dyna rules for chart display or visualization via viz_chart. Rules will
   use string formatting or python eval magic.

 - TODO: serialization

 - TODO: user-defined priorities (blocked: back-chaining)

 - TODO: filter / bulk loader; post-processing (e.g. serialization and plotting)

 - Catch typos! Warn the user if they write a predicate that is not defined on
   the LHS of a rule and it's not quoted (i.e. not some new piece of structure).
   [mode analysis will help with this].

 - If the solver is taking too long print an "apology" with some simple
   statistics explaining what the solver is doing (e.g. repropagation-rate: does
   it have a bad prioritization heuristics is it stuck in a cycle; number of
   items proved: is it counting to infinity?).


DEVELOPERS
==========

 - TODO: visualize execution of solver on hypergraph -- recreate nwf's
   animations from his ICLP talk.


BUGS
====

 - TODO: write all files to ~/.dyna

 - TODO: (@nwf) String quoting (see example/stringquote.py)

 - TODO: (@nwf) mode planning failures are slient

 - TODO: make sure interpreter uses the right exceptions. The codegen catches a
   few things -- I think assertionerror is one them... we should probably do
   whatever this is doing with a custom exception.

 - TODO: some items only learn their aggregators late in life...


NOTES
=====

 - TODO: `None` does not propagate, eventually it will because of the `?` prefix
   operator.

 - TODO: Term values should only be aggregated with ``=`` or ``:=`` maybe even
   ``set=``. We should disallow ``a += &b.``

     Equals aggregation only one value allowed, mult. >0 on single value. The
     following program has one FP of `c` end `e` are mutually exclusive.

       a = b for c
       a = d for e

     This might not be the case during computation -- this is the same as the
     error problem.

 - TODO: Numeric precision is an issue with BAggregators.

     timv: Are we sure we have this bug?

     a[0.1] += 1
     a[0.1 + eps] -= 1

   Approaches:

    - arbitrary precision arithmetic

    - approximate deletion ("buckets"), find the nearest neighbor and delete it.

    - hybrid: maintain streaming sum parallel to the BAggregator and check
      periodically for quality and null.

    - numeric approximations, stream folding (fails to get null)

    - delete the hyperedge: not sure this is perfect because hyperedges aren't
      named with numeric values of variables.


JUST FOR FUN
============

 - overload everything so that values maintain provenance and we can inspect the
   entire fine-grained circuit.

 - play around with cool python modules:

   - uncertainties (error propagation and gradients), look

   - values with units (i.e dimensional analysis).

   - sympy?


What is null?
=============

 Consider the following two similar programs

  1) a += b + c.

  2) a += b.
     a += c.

 These programs have different meanings! We can demonstrate by adding evidence.

 What should `a += null` and `a += null + 1.` do?

"""

from __future__ import division
import os, sys, imp
from collections import defaultdict
from argparse import ArgumentParser

from hashlib import sha1

import debug
from chart import Chart, Term, _repr
from defn import aggregator
from utils import ip, red, green, blue, magenta, yellow, \
    notimplemented, parse_attrs, ddict, dynac, enable_crash_handler, \
    DynaCompilerError, DynaInitializerException
from prioritydict import prioritydict
from config import dotdynadir, dynahome

try:
    from numpy import log, exp, sqrt
    from numpy.random import uniform
except ImportError:                       # XXX: should probably issue a warning.
    from math import log, exp, sqrt
    from random import random as _random
    def uniform(a=0, b=1):
        return _random() * (b - a) + a

from time import time


class Rule(object):
    def __init__(self, idx):
        self.idx = idx
        self.init = None
        self.updaters = []
    @property
    def span(self):
        return parse_attrs(self.init)['Span']
    @property
    def src(self):
        return parse_attrs(self.init)['rule']
    def __repr__(self):
        return 'Rule(%s, %r)' % (self.idx, self.src)


# TODO: yuck, hopefully temporary measure to support pickling the Interpreter's
# state
class foo(dict):
    def __init__(self, agg_name):
        self.agg_name = agg_name
        super(foo, self).__init__()
    def __missing__(self, fn):
        arity = int(fn.split('/')[-1])
        self[fn] = c = Chart(fn, arity, self.agg_name[fn])
        return c


def none():
    return None


class Interpreter(object):

    def __init__(self):
        # declarations
        self.agg_name = defaultdict(none)
        self.edges = defaultdict(set)         # TODO: finding HG should be a post-processor
        self.updaters = defaultdict(list)
        # data structures
        self.agenda = prioritydict()
        self.parser_state = ''

        self.chart = foo(self.agg_name)
        self.rules = ddict(Rule)
        self.errors = {}

    def __getstate__(self):
        return ((self.chart,
                 self.agenda,
                 self.errors,
                 self.agg_name,
                 self.parser_state),
                '\n'.join(self.rules[i].src for i in sorted(self.rules)))

    def __setstate__(self, state):
        ((self.chart, self.agenda, self.errors, self.agg_name, self.parser_state), code) = state
        self.edges = defaultdict(set)
        self.updaters = defaultdict(list)
        self.rules = ddict(Rule)
        self.do(self.dynac_code(code), initialize=False)

    def new_fn(self, fn, agg):
        # check for aggregator conflict.
        if self.agg_name[fn] is None:
            self.agg_name[fn] = agg

            # if we have a new aggregator and an existing chart we need to shove
            # a bunch of aggregators into the interned nodes.
            #
            # This happens when a new rule (e.g. from the repl) gives something
            # a value, which didn't have a value before -- i.e. was only used as
            # structure.
            if fn in self.chart:
                c = self.chart[fn]
                assert c.agg_name is None
                c.agg_name = agg
                for item in c.intern.itervalues():
                    assert c.aggregator is None
                    item.aggregator = c.new_aggregator()

        assert self.agg_name[fn] == agg, (fn, self.agg_name[fn], agg)

    def collect_edges(self):
        """
        Use rule initializers to find all active hyperedges in the current
        Chart.
        """
        edges = self.edges
        def _emit(item, _, ruleix, variables):
            b = variables['nodes']
            b.sort()
            b = tuple(b)
            edges[item].add((ruleix, b))
        for r in self.rules.values():
            r.init(emit=_emit)

    def dump_charts(self, out=sys.stdout):
        print >> out
        print >> out, 'Charts'
        print >> out, '============'
        fns = self.chart.keys()
        fns.sort()
        for x in fns:
            print >> out, self.chart[x]
            print >> out
        self.dump_errors(out)

    def dump_errors(self, out=sys.stdout):
        # We only dump the error chart if it's non empty.
        if not self.errors:
            return
        print >> out
        print >> out, 'Errors'
        print >> out, '============'
        for item, (val, es) in self.errors.items():
            print >> out,  'because %r is %s:' % (item, _repr(val))
            for e, h in es:
                if h is not None:
                    r = h.dyna_rule
                    print >> out, '    %s\n        in rule %s\n            %s' % (e, r.span, r.src)
        print >> out

    def dump_rules(self):
        for i in sorted(self.rules):
            print '%3s: %s' % (i, self.rules[i].src)

#    def query(self, q):
#        if q.endswith('.'):
#            print "Queries don't end with a dot."
#            return
#
#        query = 'out("%s") dict= %s.' % (q, q)
#
#        src = self.dynac_code(query)   # might raise DynaCompilerError
#        self.do(src)
#
#        try:
#            [(_, _, results)] = self.chart['out/1'][q,:]
#        except ValueError:
#            print 'No results.'
#            return
#
#        for val, bindings in results:
#            print '   ', val, 'when', bindings
#        print

    def build(self, fn, *args):
        # TODO: codegen should handle true/0 is True and false/0 is False
        if fn == "true/0":
            return True
        if fn == "false/0":
            return False

        # FIXME:
        if fn not in self.agg_name:
            # item has no aggregator (e.g purely structural stuff) -- what
            # happens if we add one later?
            self.new_fn(fn, None)

        return self.chart[fn].insert(args)

#    def retract_item(self, item):
#        """
#        For the moment we only correctly retract leaves.
#
#        If you retract a non-leaf item, you run the risk of it being
#        rederived. In the case of cyclic programs the derivation might be the
#        same or different.
#        """
#        # and now, for something truely horrendous -- look up an item by it's
#        # string value! This could fail because of whitespace or trivial
#        # formatting differences.
#        items = {}
#        for c in self.chart.values():
#            for i in c.intern.values():
#                items[str(i)] = i
#        try:
#            item = items[item]
#        except KeyError:
#            print 'item not found. This could be because of a trivial formatting differences...'
#            return
#        self.emit(item, item.value, None, sys.maxint, delete=True)
#        return self.go()

    def retract_rule(self, idx):
        "Retract rule and all of it's edges."
        try:
            rule = self.rules.pop(idx)
        except KeyError:
            print 'Rule %s not found.' % idx
            return
        # Step 1: remove update handlers
        for u in rule.updaters:
            for xs in self.updaters.values():
                if u in xs:
                    xs.remove(u)
                    assert u not in xs, 'Several occurrences of u in xs'
        # Step 2: run initializer in delete mode
        rule.init(emit=self.delete_emit)
        # Step 3; go!
        return self.go()

    def go(self):
        try:
            return self._go()
        except KeyboardInterrupt:
            print '^C'
            self.dump_charts()

    def _go(self):
        "the main loop"
        changed = {}
        agenda = self.agenda
        errors = self.errors
        while agenda:
            item = agenda.pop_smallest()
            was = item.value
            try:
                now = item.aggregator.fold()
            except (ZeroDivisionError, TypeError, KeyboardInterrupt, NotImplementedError) as e:
                errors[item] = ('failed to aggregate %r' % item.aggregator, [(e, None)])
                continue
            if was == now:
                continue
            was_error = False
            if item in errors:    # clear error
                was_error = True
                del errors[item]
            # TODO: handle `was` and `now` at the same time to avoid the two passes.
            # TODO: will need to propagate was=None when we have question mark
            if was is not None and not was_error:
                # if `was` is marked as an error we know it didn't propagate.
                # Thus, we can skip the delete-updates.
                self.update_dispatcher(item, was, delete=True)
            item.value = now
            if now is not None:
                self.update_dispatcher(item, now, delete=False)
            changed[item] = now
        return changed

    def update_dispatcher(self, item, val, delete):
        """
        Passes update to relevant handlers. Catches errors.
        """

        # store emissions, make sure all of them succeed before propagating
        # changes to aggregators.
        emittiers = []
        t_emit = lambda item, val, ruleix, variables: \
            emittiers.append((item, val, ruleix, variables, delete))

        errors = []

        for handler in self.updaters[item.fn]:
            try:
                handler(item, val, emit=t_emit)
            except (TypeError, ZeroDivisionError, KeyboardInterrupt, OverflowError) as e:
                errors.append((e, handler))

        if errors:
            self.errors[item] = (val, errors)
            return

        # no exception, accept emissions.
        for e in emittiers:
            # an error could happen here, but we assume (by contract) that
            # this is not possible.
            self.emit(*e)

    def new_updater(self, fn, ruleix, handler):
        self.updaters[fn].append(handler)
        rule = self.rules[ruleix]
        rule.updaters.append(handler)
        handler.rule = rule

    def new_initializer(self, ruleix, init):
        rule = self.rules[ruleix]
        assert rule.init is None
        rule.init = init
        init.rule = rule

    def delete_emit(self, item, val, ruleix, variables):
        self.emit(item, val, ruleix, variables, delete=True)

    def emit(self, item, val, ruleix, variables, delete):
        if delete:
            item.aggregator.dec(val, ruleix, variables)
        else:
            item.aggregator.inc(val, ruleix, variables)
#        self.agenda[item] = 0   # everything is high priority
        self.agenda[item] = time()  # FIFO

    def repl(self, hist = dotdynadir / 'dyna.hist'):
        import repl
        repl.REPL(self, hist).cmdloop()

    def do(self, filename, initialize=True):
        """
        Compile, load, and execute new dyna rules.

        To support the REPL, we try do load these new rules in a transaction --
        if any rule in the newly loaded code is "bad," we simple reject the
        addition of all these the new rules.

        A rule is bad if the compiler rejects it or it's initializer fails.
        """
        assert os.path.exists(filename)

        env = imp.load_source('module.name', filename)

        for k,v in [('chart', self.chart),
                    ('build', self.build),
                    ('peel', peel),
                    ('uniform', uniform), ('log', log), ('exp', exp), ('sqrt', sqrt)]:
            setattr(env, k, v)

        emits = []
        def _emit(*args):
            emits.append(args)

        # TODO: this should be a transaction.
        for k, v in env.agg_decl.items():
            self.new_fn(k, v)

        try:
            if initialize:
                # only run new initializers
                for _, init in env.initializers:
                    init(emit=_emit)

        except (TypeError, ZeroDivisionError) as e:
            raise DynaInitializerException(e, init)

        else:

            # TODO: how do I make this transactional? what it the user hits ^C
            # in the middle of the following blocK?
            #
            #   - maybe transaction isn't want I mean. Maybe all I want (for now
            #     is to avoid ^C.

            # add new updaters
            for fn, r, h in env.updaters:
                self.new_updater(fn, r, h)
            # add new initializers
            for r, h in env.initializers:
                self.new_initializer(r, h)
            # accept the new parser state
            self.parser_state = env.parser_state
            # process emits
            for e in emits:
                self.emit(*e, delete=False)

        return self.go()

    def draw(self):
        debug.draw(self)

    def dynac_code(self, code):
        """
        Compile a string of dyna code.

        raises ``DynaCompilerError``
        """
        x = sha1()
        x.update(self.parser_state)
        x.update(code)

        dyna = dotdynadir / 'tmp' / ('%s.dyna' % x.hexdigest())
        dyna.dirname().mkdir_p()  # make necessary directories

        out = '%s.plan.py' % dyna

        with file(dyna, 'wb') as f:
            f.write(self.parser_state)  # include parser state if any.
            f.write(code)

        dynac(dyna, out)   # might raise compiler error

        return out


def peel(fn, item):
    """
    Find item's args in the appropriate chart. Assert that idx matches
    functor/arity, `fn`. Returns the arguments of term as a tuple of intern idxs
    and constants (possibly an empty tuple).
    """

    if fn == "true/0" :
#        assert item is True
        assert bool(item)
        return
    if fn == "false/0" :
#        assert item is False
        assert not bool(item)
        return
    assert isinstance(item, Term)
    assert item.fn == fn
    return item.args


def main():

    parser = ArgumentParser(description="The dyna interpreter!")
    parser.add_argument('source', help='Path to Dyna source file (or plan if --plan=true).', nargs='?')
    parser.add_argument('--plan', action='store_true', default=False,
                        help='`source` specifies output of the compiler instead of dyna source code.')
    parser.add_argument('-i', dest='interactive', action='store_true', help='Fire-up an IPython shell.')
    parser.add_argument('-o', dest='output', help='Output chart.')
    parser.add_argument('--draw', action='store_true',
                        help='Output html page with hypergraph and chart.')
    parser.add_argument('--post-process', help='run post-processing script.')

    parser.add_argument('--profile', action='store_true',
                        help='run profiler.')

    args = parser.parse_args()

    if args.post_process is not None:
        try:
            pp = __import__(args.post_process)
        except ImportError:
            print ('ERROR: No postprocessor named %r' % args.post_process)
            return

    interp = Interpreter()

    enable_crash_handler()


    if args.profile:
        # When profiling, its common practice to disable the garbage collector.
        import gc
        gc.disable()

        # However, it's worth noting that the garbage collector is a real part
        # of the runtime. Some implementation details will change how often it
        # runs. For example, when we call emit with the variable binding
        # dictionary this increases refcounts to a bunch of variables. For some
        # reason the garbage collector overhead is lowered in this
        # situation. Giving in a counterintuitive result.

        from cProfile import Profile

        plan = '%s.plan.py' % args.source
        dynac(args.source, plan)

        p = Profile()
        p.runctx('interp.do(plan)', globals(), locals())
        p.dump_stats('prof')

        interp.dump_charts()

        # call graph
        os.system('gprof2dot.py -f pstats prof | dot -Tsvg -o prof.svg && eog prof.svg &')
        os.system('pkill snakeviz; snakeviz prof &')

        return


    if args.source:

        if not os.path.exists(args.source):
            print 'File %r does not exist.' % args.source
            return

        if args.plan:
            plan = args.source
        else:
            plan = "%s.plan.py" % args.source
            dynac(args.source, plan)

        interp.do(plan)

        if args.output:
            if args.output == "-":
                interp.dump_charts(sys.stdout)
            else:
                with file(args.output, 'wb') as f:
                    interp.dump_charts(f)
        else:
            interp.dump_charts()

        if args.interactive:
            interp.repl(hist = args.source + '.hist')

    else:
        interp.repl()

    if args.draw:
        interp.draw()

    if args.post_process is not None:
        pp.main(interp)


if __name__ == '__main__':
    main()
